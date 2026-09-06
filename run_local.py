 #!/usr/bin/env python3
"""
SpamProtection - Local Development Launcher
This script helps you run the application locally with proper setup.
"""

import os
import sys
import subprocess
import platform
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 9):
        print("❌ Error: Python 3.9 or higher is required")
        print(f"Current version: {sys.version}")
        sys.exit(1)
    print(f"✅ Python version: {sys.version.split()[0]}")

def check_virtual_environment():
    """Check if virtual environment is activated"""
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("✅ Virtual environment is activated")
        return True
    else:
        print("⚠️  Virtual environment not detected")
        return False

def install_requirements():
    """Install required packages"""
    print("📦 Installing requirements...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Requirements installed successfully")
    except subprocess.CalledProcessError:
        print("❌ Failed to install requirements")
        sys.exit(1)

def check_env_file():
    """Check if .env file exists"""
    env_file = Path('.env')
    if not env_file.exists():
        print("⚠️  .env file not found")
        print("Creating .env file from template...")
        
        # Copy from .env.example
        example_file = Path('.env.example')
        if example_file.exists():
            with open(example_file, 'r') as src:
                content = src.read()
            with open(env_file, 'w') as dst:
                dst.write(content)
            print("✅ .env file created from template")
            print("🔧 Please edit .env file with your Google OAuth credentials")
        else:
            print("❌ .env.example file not found")
    else:
        print("✅ .env file found")

def setup_nltk_data():
    """Download required NLTK data - fast version"""
    print("📚 Setting up NLTK data...")
    try:
        import nltk
        import os
        
        # Set custom download directory to avoid permission issues
        nltk_data_path = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'nltk_data')
        if not os.path.exists(nltk_data_path):
            os.makedirs(nltk_data_path)
        nltk.data.path.append(nltk_data_path)
        
        # Download with explicit path - quiet mode
        nltk.download('punkt', quiet=True, download_dir=nltk_data_path)
        nltk.download('stopwords', quiet=True, download_dir=nltk_data_path)
        nltk.download('wordnet', quiet=True, download_dir=nltk_data_path)
            
        print("✅ NLTK data ready")
    except ImportError:
        print("⚠️  NLTK not installed, skipping data download")
    except Exception as e:
        print(f"⚠️  NLTK setup warning: {str(e)}")

def train_model():
    """Train the spam detection model"""
    model_path = Path('models/spam_model.joblib')
    if not model_path.exists():
        print("🤖 Training spam detection model...")
        try:
            os.chdir('models')
            subprocess.check_call([sys.executable, "train_model.py"])
            os.chdir('..')
            print("✅ Model trained successfully")
        except subprocess.CalledProcessError:
            print("⚠️  Model training failed, using default model")
            os.chdir('..')
    else:
        print("✅ Trained model found")

def run_application():
    """Run the Flask application"""
    print("🚀 Starting SpamProtection application...")
    print("📱 Application will be available at: http://localhost:5000")
    print("🔍 Press Ctrl+C to stop the server")
    print("-" * 50)
    
    # Set environment variables
    os.environ['FLASK_APP'] = 'app.py'
    os.environ['FLASK_ENV'] = 'development'
    
    try:
        subprocess.run([sys.executable, "app.py"])
    except KeyboardInterrupt:
        print("\n👋 Application stopped by user")

def main():
    """Main function"""
    print("🛡️  SpamProtection - Local Development Setup")
    print("=" * 50)
    
    # Change to project directory
    project_dir = Path(__file__).parent
    os.chdir(project_dir)
    print(f"📁 Working directory: {project_dir}")
    
    # Check system requirements
    check_python_version()
    
    # Check virtual environment
    if not check_virtual_environment():
        print("\n💡 Recommendation:")
        print("   Create and activate a virtual environment:")
        if platform.system() == "Windows":
            print("   python -m venv venv")
            print("   venv\\Scripts\\activate")
        else:
            print("   python -m venv venv")
            print("   source venv/bin/activate")
        print("\n❓ Continue anyway? (y/N): ", end="")
        response = input().lower()
        if response != 'y':
            sys.exit(0)
    
    print()
    
    # Install requirements
    install_requirements()
    
    # Check environment file
    check_env_file()
    
    # Setup NLTK data
    setup_nltk_data()
    
    # Train model if needed
    train_model()
    
    print()
    print("✨ Setup complete! Starting application...")
    print()
    
    # Run the application
    run_application()

if __name__ == "__main__":
    main()
