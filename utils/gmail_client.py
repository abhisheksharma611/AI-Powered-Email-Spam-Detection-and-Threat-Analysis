import base64
import email
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import re

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
        """
        import time
        from googleapiclient.errors import HttpError
        
        try:
            # Get list of message IDs
            results = self.service.users().messages().list(
                userId='me',
                maxResults=max_results,
                q=query,
                fields='messages(id),nextPageToken'
            ).execute()
            
            messages = results.get('messages', [])
            
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
            
            # Split into batches of 20 to avoid rate limiting
            batch_size = 20
            for batch_start in range(0, len(messages), batch_size):
                batch_end = min(batch_start + batch_size, len(messages))
                batch = self.service.new_batch_http_request()
                
                for i in range(batch_start, batch_end):
                    message = messages[i]
                    request = self.service.users().messages().get(
                        userId='me',
                        id=message['id'],
                        format='full',
                        fields='id,threadId,labelIds,snippet,payload/headers,payload/parts/mimeType,payload/parts/body/data,payload/parts/body/attachmentId'
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
                            fields='id,threadId,labelIds,snippet,payload/headers,payload/parts/mimeType,payload/parts/body/data,payload/parts/body/attachmentId'
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
            
            # Filter out None results
            emails = [e for e in email_results if e is not None]
            print(f"Successfully fetched {len(emails)} of {len(messages)} emails")
            return emails
            
        except Exception as e:
            print(f"Error getting emails: {str(e)}")
            return []
    
    def _process_message_response(self, message):
        """Process a single message response from batch request."""
        try:
            headers = message['payload'].get('headers', [])
            subject = self._get_header_value(headers, 'Subject')
            sender = self._get_header_value(headers, 'From')
            date = self._get_header_value(headers, 'Date')
            
            body = self._extract_body(message['payload'])
            
            email_data = {
                'id': message['id'],
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
    
    def _extract_body(self, payload):
        """Extract email body from payload"""
        try:
            body = ''
            
            if 'parts' in payload:
                # Multipart message
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                        body = self._decode_base64(part['body']['data'])
                        break
                    elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
                        html_body = self._decode_base64(part['body']['data'])
                        body = self._html_to_text(html_body)
            else:
                # Single part message
                if payload.get('mimeType') == 'text/plain' and 'data' in payload.get('body', {}):
                    body = self._decode_base64(payload['body']['data'])
                elif payload.get('mimeType') == 'text/html' and 'data' in payload.get('body', {}):
                    html_body = self._decode_base64(payload['body']['data'])
                    body = self._html_to_text(html_body)
            
            return body or ''
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
                return {'html': html_body, 'text': text_body, 'type': 'html'}
            elif text_body:
                return {'html': '', 'text': text_body, 'type': 'text'}
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
    
    def _html_to_text(self, html):
        """Convert HTML to plain text"""
        try:
            if not html:
                return ''
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            # Clean up whitespace
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