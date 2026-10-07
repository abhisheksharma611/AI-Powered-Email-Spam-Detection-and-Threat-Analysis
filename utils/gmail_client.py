import base64
import email
import html as html_mod
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import re


class GmailFetchError(RuntimeError):
    """Raised when fetching from Gmail fails.

    Exists so a broken fetch cannot be reported to the user as an empty inbox.
    get_recent_emails() used to catch everything and return [], which made
    "your token expired", "the network dropped" and "you have no mail in that
    date range" all produce the same empty list, and both scan paths then said
    "Inbox appears empty".
    """

# Partial-response mask for a single message.
#
# The previous mask was:
#   id,threadId,labelIds,snippet,payload/headers,
#   payload/parts/mimeType,payload/parts/body/data,payload/parts/body/attachmentId
#
# Gmail expands `payload/parts/...` against the FIRST level of parts only. It does
# not descend into nested multipart/* wrappers, and it never returns
# `payload/body/data` for the root. Measured consequence: every marketing email
# that arrives as text/html inside a multipart/alternative wrapper came back with
# no body at all, so _extract_body returned '' and app.py fell back to Gmail's
# ~36-word snippet. The classifier was therefore judging every real email on a
# snippet, which is why `body` was empty in all 694 stored rows.
#
# `payload` alone returns the whole subtree, so nested parts and the root body
# both arrive. Attachment bytes are NOT included: Gmail only puts an
# `attachmentId` on those and requires a separate GET to fetch the content, so
# this mask does not pull file contents down the wire.
_MESSAGE_FIELDS = 'id,threadId,labelIds,snippet,payload'

# Word ceiling for text handed to the classifier.
#
# Feeding a real body UNCAPPED makes classification worse. A Gmail body runs
# 300-600 words, which is 400-800 tokens, so RoBERTa truncates and reads only the
# unsubscribe footer, nav menu and terms of service. Measured on the Drive-share
# mail:
#
#     subject only     4 words    -> Malware    0.828
#     + 60 word body  70 words    -> legitimate 0.927
#     + 200 words     144 words    -> Phishing   0.630
#     + 600 words     329 words    -> Phishing   0.740
#
# and the same inversion happens to a promotion ("Payday Sale" 0.968 at 40 words,
# Phishing 0.535 at 520 words).
#
# 200 words. A real Gmail body runs 300-600 words, and the footer block is cut
# before this cap runs, so what survives at 200 words is still the message body
# rather than boilerplate. Below the body length, a cap only buys training-time
# truncation that the pipeline never reproduces.
#
# Measured token cost: the training corpus runs 1.16 tokens/word at the median
# and 1.41 at p99, so 200 words is 232 tokens at p50 and 282 at p99. The model
# must be given room for that, which is why MAX_LEN in models/roberta_train.py
# is 384 rather than 256. Measured on a 200-word string, the worst ratio in the
# corpus (1.75, a row dense with proper nouns and an email address) reaches 349.
#
# The stored `body` column keeps the full text: the cap applies only to the
# string passed to the model, so the UI and any audit still see everything.
MODEL_INPUT_MAX_WORDS = 200

# Trailing boilerplate that is pure noise and is best dropped before capping,
# because in a long email it is the part that survives truncation.
# Phrases that only ever appear in legal/disclaimer/unsubscribe blocks. These are
# deliberately NOT topic words: an earlier version included 'newsletter', which
# cut legitimate newsletter bodies in half because the word describes the message
# rather than marking boilerplate.
_FOOTER_MARKERS = (
    'unsubscribe', 'view in browser', 'view this email', 'view it in your browser',
    'manage preferences', 'email preferences', 'you are receiving this',
    "you're receiving this", 'you got this email because', 'privacy policy',
    'terms of service', 'terms and conditions', 'terms & conditions',
    'all rights reserved', 'copyright', 'follow us', 'add us on',
    'was sent to', 'this email was sent', 'if you no longer wish',
    'do not reply to this email', 'this is a system message',
    'this message was sent', 'our apologies for any inconvenience',
    'registered office', 'company registration', 'gstin', 'cin no',
    'to unsubscribe click', 'click here to unsubscribe', 'update your preferences',
)

# A footer block is only cut when the text from the candidate point to the end is
# itself mostly boilerplate. Requiring two or more markers, or a very short tail,
# stops a legitimate message that happens to mention "unsubscribe" mid-body from
# being truncated at that word.
_FOOTER_MIN_SCORE = 2


def _looks_like_footer(tail):
    """True when a text run reads as boilerplate rather than prose.

    Marker DENSITY is the primary signal, because a real legal block names
    itself several times: the real Payday Sale tail contains nine distinct
    markers (unsubscribe, view in browser, privacy policy, terms and conditions,
    all rights reserved, do not reply, and so on). No prose paragraph does that.

    The prose heuristics below are only consulted for a tail with ONE marker,
    where density cannot decide. That tail is a passing mention in a sentence
    rather than the start of a footer block, and it must not be cut. An earlier
    version applied the punctuation test to every tail and consequently refused
    to cut genuine footers, because a real footer is written in short sentences
    and therefore has plenty of full stops.
    """
    if not tail or not tail.strip():
        return False

    distinct = sum(1 for m in set(_FOOTER_MARKERS) if m in tail)
    if distinct >= 3:
        return True
    if distinct == 2:
        return True

    # Exactly one marker: decide with prose shape.
    words = tail.split()
    if len(words) < 8:
        return True
    avg_word_len = sum(len(w) for w in words) / len(words)
    if avg_word_len > 5.4:
        return False          # long words = ordinary prose
    if ';' in tail:
        return False          # a semicolon means a sentence continues
    return True


def cap_for_model(text, max_words=MODEL_INPUT_MAX_WORDS):
    """Cap the text handed to the classifier.

    Module-level so app.py shares one definition with the client rather than
    re-deriving the budget. The stored body is never capped; only this string is.
    """
    if not text:
        return ''
    words = str(text).split()
    if len(words) <= max_words:
        return str(text)
    return ' '.join(words[:max_words])


def prepare_for_model(text, max_words=MODEL_INPUT_MAX_WORDS):
    """The one function that turns a raw body into classifier input.

    Strip the boilerplate tail, then cap. Order matters: the footer is the part
    that survives a truncation, so cutting it first is what makes the cap keep
    the message body instead.

    Module-level, and shared with models/roberta_train.py and models/evaluate.py,
    because a train/serve asymmetry here is silent: the model is trained on one
    string shape and asked to classify another, and nothing raises. Both scripts
    therefore call THIS function rather than reading the CSV raw.
    """
    return cap_for_model(GmailClient._strip_footer(text or ''), max_words)


class GmailClient:
    def __init__(self, oauth_token):
        try:
            self.credentials = Credentials(
                token=oauth_token['access_token'],
                refresh_token=oauth_token.get('refresh_token'),
                token_uri=oauth_token.get('token_uri', 'https://oauth2.googleapis.com/token'),
                client_id=oauth_token.get('client_id'),
                client_secret=oauth_token.get('client_secret'),
                scopes=oauth_token.get('scope', '').split() if isinstance(oauth_token.get('scope'), str) else oauth_token.get('scope', [])
            )
            try:
                self.service = build('gmail', 'v1', credentials=self.credentials, num_retries=3)
            except HttpError as e:
                raise RuntimeError(f"Gmail API error during initialization: {e.resp.status} {e._get_reason()}")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"Error initializing Gmail client: {str(e)}")
            raise
    
    def get_recent_emails(self, max_results=50, query=''):
        """Get recent emails from Gmail with batched fetching to avoid rate limits.

        Fetches emails in smaller batches of 20 with delays between batches
        to avoid Google's "Too many concurrent requests" (429) rate limit.
        Failed requests are retried individually with exponential backoff.

        TRASH EXCLUSION. Gmail's messages.list returns every label when no
        filter is given -- INBOX, Archive, Spam and Trash alike -- so deleted
        mail was being fetched, classified and stored. Two independent guards,
        because one is not enough to trust:

          1. the search defaults to '-in:trash', which does the work server-side
             so a trashed body is never downloaded at all
          2. every returned message is checked for the TRASH label below

        The second guard is the one that actually guarantees the behaviour. If
        the search operator ever under-delivers, the label check still stops it,
        and it costs nothing because labelIds is already in _MESSAGE_FIELDS.
        Archive and Spam are deliberately INCLUDED: archiving is not deleting,
        and the user's mailbox minus Trash is what they asked to see.
        """
        import time
        from googleapiclient.errors import HttpError

        try:
            # Get list of message IDs.
            #
            # PAGINATION. This used to make exactly one list() call and trust
            # maxResults, so a wide date range silently returned only the first
            # page -- picking "custom, all of September" would analyse 50 emails
            # and report success. Gmail caps one page at 500, so the loop pages
            # until nextPageToken is gone or the hard ceiling is reached.
            #
            # hard_cap is a safety net against a runaway range (a decade of
            # mail), not a limit on what a normal range can return.
            hard_cap = 500
            messages = []
            page_token = None
            while True:
                page = self.service.users().messages().list(
                    userId='me',
                    maxResults=min(max_results, 500),
                    q=(query or '-in:trash').strip(),
                    pageToken=page_token,
                    fields='messages(id),nextPageToken'
                ).execute()
                messages.extend(page.get('messages', []))
                page_token = page.get('nextPageToken')
                if not page_token or len(messages) >= max_results or len(messages) >= hard_cap:
                    break
            messages = messages[:max_results]

            if not messages:
                return []
            
            email_results = [None] * len(messages)
            
            # Track IDs that need retry
            retry_queue = []
            max_retries = 3
            
            def callback(request_id, response, exception):
                """Callback for batch request responses."""
                idx = int(request_id.split('_')[1])
                if exception:
                    # Check if it's a rate-limit error (429) to retry
                    is_rate_limit = (
                        isinstance(exception, HttpError) and 
                        exception.resp.status == 429
                    )
                    if is_rate_limit:
                        retry_queue.append(idx)
                        print(f"Rate limited on email {idx}, queued for retry...")
                    else:
                        print(f"Error fetching email {idx}: {exception}")
                elif response:
                    email_results[idx] = self._process_message_response(response)
            
            # Split into batches to avoid rate limiting.
            #
            # 20 -> 50. On a 500-email scan this is 25 batches with a 0.5s sleep
            # between each, i.e. 12.5 seconds of pure waiting, versus ~5s here.
            # Gmail's documented quota is 500 user messages per minute, so 10
            # batches of 50 requests is nowhere near the ceiling.
            batch_size = 50
            for batch_start in range(0, len(messages), batch_size):
                batch_end = min(batch_start + batch_size, len(messages))
                batch = self.service.new_batch_http_request()
                
                for i in range(batch_start, batch_end):
                    message = messages[i]
                    request = self.service.users().messages().get(
                        userId='me',
                        id=message['id'],
                        format='full',
                        fields=_MESSAGE_FIELDS
                    )
                    batch.add(request, callback=callback, request_id=f'msg_{i}')
                
                # Execute batch
                batch.execute()
                
                # Delay between batches to avoid rate limits
                if batch_end < len(messages):
                    time.sleep(0.5)
            
            # Retry failed requests individually with exponential backoff
            for attempt in range(max_retries):
                if not retry_queue:
                    break
                
                # Take a copy and clear the queue for this retry round
                current_retries = list(retry_queue)
                retry_queue = []
                
                # Wait before retry (exponential backoff: 1s, 2s, 4s)
                wait_time = (2 ** attempt) * 1.0
                print(f"Retrying {len(current_retries)} failed email(s) (attempt {attempt + 1}, wait {wait_time}s)...")
                time.sleep(wait_time)
                
                for idx in current_retries:
                    try:
                        message = messages[idx]
                        response = self.service.users().messages().get(
                            userId='me',
                            id=message['id'],
                            format='full',
                            fields=_MESSAGE_FIELDS
                        ).execute()
                        email_results[idx] = self._process_message_response(response)
                        print(f"Successfully retried email {idx}")
                    except HttpError as e:
                        if e.resp.status == 429:
                            retry_queue.append(idx)  # Still rate-limited, retry next round
                        else:
                            print(f"Error fetching email {idx} on retry {attempt + 1}: {e}")
                    except Exception as e:
                        print(f"Error fetching email {idx} on retry {attempt + 1}: {e}")
            
            if retry_queue:
                print(f"Warning: {len(retry_queue)} emails could not be fetched after {max_retries} retries")

            # TRASH GUARD. The label is on the message we already fetched, so
            # this is free. A trashed message is dropped here and therefore
            # never reaches _prepare_email_data, is never classified, and is
            # never written to the database.
            trashed = [e for e in email_results
                       if e is not None and 'TRASH' in set(e.get('label_ids') or [])]
            if trashed:
                email_results = [None if (e is not None and
                                          'TRASH' in set(e.get('label_ids') or []))
                                 else e for e in email_results]
                print(f"Excluded {len(trashed)} trashed email(s) from this scan")

            # Filter out None results
            emails = [e for e in email_results if e is not None]
            print(f"Successfully fetched {len(emails)} of {len(messages)} emails")
            return emails

        except Exception as e:
            # This used to `print` and return [], which made an expired OAuth
            # token, a network failure and a genuinely empty mailbox all look
            # identical to the caller. Both scan paths then reported "Inbox
            # appears empty", so a real failure was shown to the user as an
            # absence of mail. It is raised now and reported honestly.
            print(f"Error getting emails: {str(e)}")
            raise GmailFetchError(str(e)) from e

    def get_trashed_ids(self, max_results=200):
        """Ids currently sitting in Trash. Ids only, no bodies.

        Used to purge stored rows for mail the user has deleted. The scan only
        ever sees the newest max_results messages, so "not in the last 50" does
        NOT mean deleted -- it usually just means older. Asking Gmail which of
        the ids we hold are in Trash is the only precise way to prune, and it
        costs one id-only listing instead of 50 message fetches.
        """
        try:
            found, token = set(), None
            while True:
                resp = self.service.users().messages().list(
                    userId='me',
                    q='in:trash',
                    maxResults=max_results,
                    pageToken=token,
                    fields='messages(id),nextPageToken'
                ).execute()
                found.update(m['id'] for m in resp.get('messages', []))
                token = resp.get('nextPageToken')
                if not token:
                    break
            return found
        except Exception as e:
            # Returning None, not an empty set, is deliberate: an empty set
            # would mean "nothing is trashed" and silently skip the purge,
            # while None tells the caller the question could not be answered.
            print(f"Error listing trashed ids: {str(e)}")
            return None
    
    def _process_message_response(self, message):
        """Process a single message response from batch request."""
        try:
            headers = message['payload'].get('headers', [])
            subject = self._get_header_value(headers, 'Subject')
            sender = self._get_header_value(headers, 'From')
            date = self._get_header_value(headers, 'Date')
            
            body = self._extract_body(message['payload'])
            snippet = message.get('snippet', '')

            # Gmail's own snippet carries raw HTML entities (&#39;, &amp;).
            # 240 of the 694 real rows had them and no training row does, so the
            # model was reading markup noise on real mail only. Decode here so
            # subject, snippet and body all reach the classifier as plain text.
            if snippet:
                snippet = html_mod.unescape(snippet)
            subject_decoded = html_mod.unescape(subject) if subject else subject

            email_data = {
                'id': message['id'],
                'subject': subject_decoded or 'No Subject',
                'sender': self._clean_email_address(sender) or 'Unknown',
                'date': self._parse_date(date) or 'Unknown Date',
                'body': body or '',
                'snippet': snippet or '',
                'thread_id': message.get('threadId', ''),
                'label_ids': message.get('labelIds', [])
            }
            
            return email_data
        except Exception as e:
            print(f"Error processing message response: {str(e)}")
            return None
    
    def get_email_content(self, message_id):
        """Get content of a specific email"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extract headers
            headers = message['payload'].get('headers', [])
            subject = self._get_header_value(headers, 'Subject')
            sender = self._get_header_value(headers, 'From')
            date = self._get_header_value(headers, 'Date')
            
            # Extract body
            body = self._extract_body(message['payload'])
            
            # Create email object
            email_data = {
                'id': message_id,
                'subject': subject or 'No Subject',
                'sender': self._clean_email_address(sender) or 'Unknown',
                'date': self._parse_date(date) or 'Unknown Date',
                'body': body or '',
                'snippet': message.get('snippet', ''),
                'thread_id': message.get('threadId', ''),
                'label_ids': message.get('labelIds', [])
            }
            
            return email_data
            
        except Exception as e:
            print(f"Error getting email content for {message_id}: {str(e)}")
            return None
    
    def _get_header_value(self, headers, name):
        """Extract header value by name"""
        try:
            for header in headers:
                if header.get('name') == name:
                    return header.get('value', '')
            return ''
        except Exception:
            return ''
    
    @staticmethod
    def _strip_footer(text):
        """Drop the trailing unsubscribe/terms block from a body.

        In a 400-word marketing email the boilerplate is exactly the part that
        survives the model's 256-token truncation, so it would be the part the
        model actually reads. Cutting it first means the cap keeps the message
        body instead.

        The first version required a marker to sit past 60% of the string, which
        missed the common layout where a short offer block is followed by a long
        legal tail: the real Payday Sale body put "Unsubscribe" at 40% and the
        whole legal section survived into the model input, flipping that mail from
        promotion 0.951 to phishing 0.598.

        The rule is now: find the earliest marker that is not in the opening
        sentence, then require the tail from there to the end to be mostly
        boilerplate (two or more markers, or a short tail). Requiring the tail to
        actually look like a footer is what stops a legitimate message that says
        "unsubscribe" once mid-body from being cut in half at that word.
        """
        if not text or len(text.split()) < 60:
            return text
        low = text.lower()
        n = len(text)

        # Never cut the opening: a subject line or salutation must survive.
        first_sentence_end = low.find('.')
        floor = max(first_sentence_end + 1, int(n * 0.08))

        hits = []
        for marker in _FOOTER_MARKERS:
            start = 0
            while True:
                i = low.find(marker, start)
                if i < 0:
                    break
                if i >= floor:
                    hits.append((i, marker))
                start = i + 1
        if not hits:
            return text

        hits.sort()
        for cut_at, _marker in hits:
            tail = low[cut_at:]
            # Count DISTINCT markers in the tail: a real footer names itself twice
            # or more, a passing mention does not.
            distinct = sum(1 for m in set(_FOOTER_MARKERS) if m in tail)
            tail_len = n - cut_at
            # A tail is only a footer block if it names itself more than once.
            # One marker in a long tail is a passing mention in a sentence.
            if distinct < _FOOTER_MIN_SCORE and tail_len >= n * 0.12:
                continue
            # The tail must read like a footer, not like a sentence that happens
            # to contain a marker. A real legal block is a run of short lines with
            # no verbs of instruction; prose keeps sentence punctuation and longer
            # stretches between words. This stops a phishing row like "..., then
            # this message was sent to the address on the membership, and if the
            # address is the one ..." from being cut in half mid-argument.
            if not _looks_like_footer(tail):
                continue
            kept = text[:cut_at].rstrip(' .,;:-')
            return kept if len(kept.split()) >= 25 else text
        return text

    @staticmethod
    def _cap_for_model(text, max_words=MODEL_INPUT_MAX_WORDS):
        """Cap text to the word budget the model was actually trained within.

        See MODEL_INPUT_MAX_WORDS. The stored body is never capped; only the
        string handed to the classifier is.
        """
        return cap_for_model(text, max_words)

    def _extract_body(self, payload):
        """Extract the list-view body from a message payload.

        Rich Gmail mail is normally text/html, often with no text/plain part at
        all. The previous version walked the top-level parts and took the first
        text/plain it found, so a purely HTML message produced an empty body and
        the model only ever saw Gmail's own snippet. That is why all 694 stored
        rows had an empty body and a ~36 word snippet where the training rows
        average 76 words.

        Now every MIME part is walked recursively, HTML is preferred over plain
        because it carries the banner and offer copy, and both are cleaned of
        entities. Plain text is used as the fallback when HTML is absent.
        """
        try:
            html_body, text_body = '', ''

            def walk(parts):
                """Collect the first HTML and first plain body, deepest last."""
                nonlocal html_body, text_body
                for part in parts:
                    if 'parts' in part:
                        walk(part['parts'])
                        continue
                    mime = part.get('mimeType', '')
                    data = part.get('body', {}).get('data')
                    if not data:
                        continue
                    if mime == 'text/html' and not html_body:
                        html_body = self._decode_base64(data)
                    elif mime == 'text/plain' and not text_body:
                        text_body = self._decode_base64(data)

            if 'parts' in payload:
                walk(payload['parts'])
            else:
                mime = payload.get('mimeType', '')
                data = payload.get('body', {}).get('data')
                if data:
                    if mime == 'text/html':
                        html_body = self._decode_base64(data)
                    elif mime == 'text/plain':
                        text_body = self._decode_base64(data)

            if html_body:
                # keep_visual=True: alt text and button copy, which is the
                # "banners and photos" content the model should learn from.
                return self._strip_footer(
                    self._html_to_text(html_body, keep_visual=True))
            if text_body:
                return self._strip_footer(
                    html_mod.unescape(text_body).strip())
            return ''
        except Exception as e:
            print(f"Error extracting body: {str(e)}")
            return ''
    
    def get_full_email(self, message_id):
        """
        Get full email content with HTML body preserved for display.
        
        This method fetches the complete email and extracts:
        - Subject
        - Sender (From)
        - Date
        - Full HTML body (preferred) or plain text fallback
        
        Handles multipart emails safely and nested MIME parts.
        
        Args:
            message_id: Gmail message ID
            
        Returns:
            dict: Email data with full body content, or None if error
        """
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extract headers
            headers = message['payload'].get('headers', [])
            subject = self._get_header_value(headers, 'Subject')
            sender = self._get_header_value(headers, 'From')
            date = self._get_header_value(headers, 'Date')
            to = self._get_header_value(headers, 'To')
            cc = self._get_header_value(headers, 'Cc')
            
            # Extract full body (HTML preferred, plain text fallback)
            body_result = self._extract_full_body(message['payload'])
            
            # Parse date and convert to IST for display
            parsed_dt = self._parse_date_aware(date) if date else None
            
            # Create email object with full content
            email_data = {
                'id': message_id,
                'subject': subject or 'No Subject',
                'sender': self._clean_email_address(sender) or 'Unknown',
                'sender_display': sender or 'Unknown',
                'date': parsed_dt.strftime('%Y-%m-%d %I:%M %p IST') if parsed_dt else (self._parse_date(date) or 'Unknown Date'),
                'date_raw': date or '',
                'to': to or '',
                'cc': cc or '',
                'body_html': body_result.get('html', ''),
                'body_text': body_result.get('text', ''),
                'body_type': body_result.get('type', 'text'),
                'snippet': message.get('snippet', ''),
                'thread_id': message.get('threadId', ''),
                'label_ids': message.get('labelIds', [])
            }
            
            return email_data
            
        except Exception as e:
            print(f"Error getting full email content for {message_id}: {str(e)}")
            return None
    
    def _extract_full_body(self, payload):
        """
        Extract full email body with HTML preservation.
        
        Priority:
        1. HTML body (for rich formatting)
        2. Plain text fallback
        
        Handles:
        - Single part messages
        - Multipart messages (multipart/alternative, multipart/mixed)
        - Nested MIME parts
        - Missing body parts
        
        Args:
            payload: Gmail API message payload
            
        Returns:
            dict: {'html': str, 'text': str, 'type': 'html'|'text'}
        """
        try:
            html_body = ''
            text_body = ''
            
            def extract_from_parts(parts):
                """Recursively extract body from MIME parts"""
                nonlocal html_body, text_body
                
                for part in parts:
                    mime_type = part.get('mimeType', '')
                    body_data = part.get('body', {}).get('data', '')
                    
                    # Check for nested parts
                    if 'parts' in part:
                        extract_from_parts(part['parts'])
                    
                    # Extract HTML body
                    if mime_type == 'text/html' and body_data and not html_body:
                        html_body = self._decode_base64(body_data)
                    
                    # Extract plain text body
                    elif mime_type == 'text/plain' and body_data and not text_body:
                        text_body = self._decode_base64(body_data)
            
            # Handle multipart messages
            if 'parts' in payload:
                extract_from_parts(payload['parts'])
            else:
                # Single part message
                mime_type = payload.get('mimeType', '')
                body_data = payload.get('body', {}).get('data', '')
                
                if mime_type == 'text/html' and body_data:
                    html_body = self._decode_base64(body_data)
                elif mime_type == 'text/plain' and body_data:
                    text_body = self._decode_base64(body_data)
            
            # Return HTML if available, otherwise plain text
            if html_body:
                # The text field is the entity-decoded, tag-stripped version of the
                # HTML, so the full-email view and the classifier see the same
                # characters. Previously 'text' was the raw text/plain part, which
                # is empty for every HTML-only message, so the view fell back to a
                # snippet while the classifier got nothing.
                return {'html': html_body,
                        'text': self._html_to_text(html_body, keep_visual=True),
                        'type': 'html'}
            elif text_body:
                return {'html': '',
                        'text': html_mod.unescape(text_body).strip(),
                        'type': 'text'}
            else:
                return {'html': '', 'text': 'No content available', 'type': 'text'}
                
        except Exception as e:
            print(f"Error extracting full body: {str(e)}")
            return {'html': '', 'text': 'Error loading email content', 'type': 'text'}
    
    def _decode_base64(self, data):
        """Decode base64 data"""
        try:
            if not data:
                return ''
            
            # Fix padding
            data = data.replace('-', '+').replace('_', '/')
            padding = len(data) % 4
            if padding:
                data += '=' * (4 - padding)
            
            decoded_bytes = base64.b64decode(data)
            return decoded_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"Error decoding base64: {str(e)}")
            return ''
    
    # Tags whose entire subtree carries no message text. Dropping the subtree
    # instead of the tag alone removes the CSS of a marketing email, which is
    # often several kilobytes and dilutes the real copy in a 256-token window.
    _NON_TEXT_TAGS = ('script', 'style', 'head', 'noscript', 'template', 'svg')

    def _html_to_text(self, html, keep_visual=False):
        """Convert an HTML email body to plain text.

        Two defects made this the source of a train/serve skew:

        1. HTML entities were never decoded. 240 of the 694 real inbox rows carry
           `&#39;` or `&amp;` in the subject/snippet, and no training row does, so
           the model was reading markup noise on real mail and never on synthetic
           mail. html.unescape handles named (`&amp;`, `&nbsp;`), decimal
           (`&#39;`) and hex (`&#x27;`) forms in one call.
        2. Rich Gmail mail is almost always text/html, and the list scan only ever
           read text/plain. Banner text, offer blocks and footer copy were being
           dropped before the model saw them.

        Args:
            html: raw HTML body
            keep_visual: when True, keep alt/title/aria-label text from images
                and the text of buttons and links. This is the "banners and photos"
                content the user asked the model to see. Off by default because the
                visible copy alone is the cleaner signal for a 256-token window.
        """
        try:
            if not html:
                return ''

            soup = BeautifulSoup(html, 'html.parser')

            for tag_name in self._NON_TEXT_TAGS:
                for tag in soup.find_all(tag_name):
                    tag.decompose()

            if keep_visual:
                # Alt text is the only text an image-only banner contributes, so
                # promote it rather than dropping the element entirely.
                for img in soup.find_all('img'):
                    label = (img.get('alt') or img.get('title') or '').strip()
                    img.replace_with(' %s ' % label if label else ' ')

            text = soup.get_text(separator=' ')

            # Decode entities AFTER get_text, because get_text() does not touch them.
            text = html_mod.unescape(text)
            # unescape leaves the zero-width and soft-hyphen characters that
            # marketing mail uses for tracking; they otherwise reach the tokenizer.
            text = text.replace('\u200b', ' ').replace('\u200c', ' ').replace('\u200d', ' ')
            text = text.replace('\ufeff', ' ').replace('\u00ad', '')
            text = text.replace('\xa0', ' ')

            text = re.sub(r'\s+', ' ', text).strip()
            return text
        except Exception as e:
            print(f"Error converting HTML to text: {str(e)}")
            return html
    
    def _clean_email_address(self, email_string):
        """Extract clean email address"""
        try:
            if not email_string:
                return ''
            
            # Extract email from format like "Name <email@domain.com>"
            email_match = re.search(r'<([^>]+)>', email_string)
            if email_match:
                return email_match.group(1)
            
            # If no angle brackets, assume it's just the email
            email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', email_string)
            if email_match:
                return email_match.group(0)
            
            return email_string
        except Exception as e:
            print(f"Error cleaning email address: {str(e)}")
            return str(email_string) if email_string else ''
    
    def _parse_date_aware(self, date_string):
        """
        Parse email date string and return a timezone-aware datetime in UTC.
        
        This preserves the timezone offset from the email header and converts
        to UTC, so downstream code can properly convert to the user's local timezone.
        
        Gmail dates are in RFC 2822 format, e.g.:
        'Thu, 28 May 2026 12:54:00 +0000'
        'Thu, 28 May 2026 06:24:00 -0530'
        
        Args:
            date_string: RFC 2822 date string from email header
            
        Returns:
            timezone-aware datetime in UTC, or None if parsing fails
        """
        try:
            if not date_string:
                return None
            
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_string)
            
            # Convert to UTC:
            # - If timezone-aware, convert to UTC
            # - If naive, assume UTC (Gmail uses UTC internally)
            if dt.tzinfo is not None:
                dt_utc = dt.astimezone(timezone.utc)
            else:
                dt_utc = dt.replace(tzinfo=timezone.utc)
            
            return dt_utc
        except Exception as e:
            return None
    
    def _parse_date(self, date_string):
        """
        Parse email date string to UTC ISO format string.
        
        Converts RFC 2822 date to a UTC ISO 8601 format with timezone info,
        so downstream code can properly handle timezone conversion.
        
        Returns:
            ISO 8601 string like '2026-05-28T12:54:00+00:00'
        """
        try:
            if not date_string:
                return ''
            
            dt_utc = self._parse_date_aware(date_string)
            if dt_utc is None:
                return str(date_string) if date_string else ''
            
            # Return ISO format with explicit UTC offset
            result = dt_utc.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            return result
            
        except Exception as e:
            return str(date_string) if date_string else ''
    
    def get_labels(self):
        """Get Gmail labels"""
        try:
            results = self.service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])
            return labels
        except Exception as e:
            print(f"Error getting labels: {str(e)}")
            return []
    
    def search_emails(self, query, max_results=10):
        """Search emails with specific query"""
        return self.get_recent_emails(max_results=max_results, query=query)