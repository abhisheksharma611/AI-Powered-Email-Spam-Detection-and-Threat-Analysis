from authlib.integrations.flask_client import OAuth
from flask import current_app

def init_oauth(app):
    oauth = OAuth(app)
    
    # Register Google OAuth provider with proper configuration
    google = oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile https://www.googleapis.com/auth/gmail.readonly',
            'prompt': 'select_account',  # Always show account selection
        },
        # Add these for better compatibility
        authorize_params=None,
        access_token_params=None,
        refresh_token_params=None,
    )
    
    return oauth

def get_google_auth():
    from flask import current_app
    return current_app.extensions['authlib.integrations.flask_client']['google']
