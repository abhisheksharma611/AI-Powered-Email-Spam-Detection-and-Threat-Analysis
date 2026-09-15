import os
from dotenv import load_dotenv

load_dotenv()

# Get the absolute path for the database
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    # SECRET_KEY - Use env var if available, fallback to dev key with warning
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
    if not SECRET_KEY:
        import sys
        print("WARNING: FLASK_SECRET_KEY not set. Using development fallback. "
              "Set FLASK_SECRET_KEY in production!", file=sys.stderr)
        SECRET_KEY = 'dev-secret-key-change-in-prod'
    
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    
    # Database Configuration - use absolute path to ensure database is created in project directory
    db_path = os.environ.get('DATABASE_URL')
    if db_path:
        SQLALCHEMY_DATABASE_URI = db_path
    else:
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "emails.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # OAuth 2.0 Configuration
    OAUTH2_PROVIDERS = {
        'google': {
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'server_metadata_url': 'https://accounts.google.com/.well-known/openid-configuration',
            'client_kwargs': {
                'scope': 'openid email profile https://www.googleapis.com/auth/gmail.readonly',
                'access_type': 'offline',
                'prompt': 'consent',
            }
        }
    }
    
    # Gmail API Configuration
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/userinfo.profile',
        'openid'
    ]
    
    # ML Model Configuration
    MODEL_PATH = 'models/ensemble_model.joblib'
    VECTORIZER_PATH = 'models/vectorizer.joblib'

    # Local Ollama AI explanation (active — gemma2:2b only)
    OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')
    OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'gemma2:2b')
    OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '90'))
    OLLAMA_NUM_CTX = int(os.environ.get('OLLAMA_NUM_CTX', '4096'))
    OLLAMA_NUM_PREDICT = int(os.environ.get('OLLAMA_NUM_PREDICT', '100'))
    OLLAMA_KEEP_ALIVE = os.environ.get('OLLAMA_KEEP_ALIVE', '5m')

    # NVIDIA NIM (commented out for now — uncomment .env lines to re-enable)
    # NVIDIA_NIM_BASE_URL = os.environ.get('NVIDIA_NIM_BASE_URL')
    # NVIDIA_NIM_API_KEY = os.environ.get('NVIDIA_NIM_API_KEY')
    # NVIDIA_NIM_MODEL = os.environ.get('NVIDIA_NIM_MODEL')
    
    # Session Configuration
    SESSION_TYPE = 'filesystem'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    
class DevelopmentConfig(Config):
    DEBUG = False  # Disabled to prevent Flask reloader from reloading models
    DEVELOPMENT = True
    TEMPLATES_AUTO_RELOAD = True  # Enabled for template auto-reload during development

class ProductionConfig(Config):
    DEBUG = False
    DEVELOPMENT = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
