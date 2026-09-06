#!/bin/bash
# SpamProtection Deployment Script

echo "🚀 SpamProtection Deployment Script"
echo "=================================="

# Check if Heroku CLI is installed
if ! command -v heroku &> /dev/null; then
    echo "❌ Heroku CLI is not installed"
    echo "Please install it from: https://devcenter.heroku.com/articles/heroku-cli"
    exit 1
fi

echo "✅ Heroku CLI found"

# Check if logged in to Heroku
if ! heroku whoami &> /dev/null; then
    echo "🔐 Please login to Heroku:"
    heroku login
fi

echo "✅ Heroku authentication confirmed"

# Get app name
echo -n "📱 Enter your Heroku app name: "
read APP_NAME

if [ -z "$APP_NAME" ]; then
    echo "❌ App name cannot be empty"
    exit 1
fi

# Create app if it doesn't exist
echo "📋 Creating Heroku app (if not exists)..."
heroku create $APP_NAME 2>/dev/null || echo "✅ App already exists"

# Set environment variables
echo "🔧 Setting environment variables..."
echo -n "Enter your Google Client ID: "
read CLIENT_ID

echo -n "Enter your Google Client Secret: "
read -s CLIENT_SECRET
echo

echo -n "Enter a secret key for Flask: "
read -s SECRET_KEY
echo

heroku config:set GOOGLE_CLIENT_ID="$CLIENT_ID" -a $APP_NAME
heroku config:set GOOGLE_CLIENT_SECRET="$CLIENT_SECRET" -a $APP_NAME
heroku config:set FLASK_SECRET_KEY="$SECRET_KEY" -a $APP_NAME

echo "✅ Environment variables set"

# Deploy to Heroku
echo "🚀 Deploying to Heroku..."
git add .
git commit -m "Deploy to Heroku" 2>/dev/null || echo "No changes to commit"
git push heroku main

echo "✅ Deployment complete!"
echo "🌐 Your app is available at: https://$APP_NAME.herokuapp.com"
echo "📋 Don't forget to add the callback URL to your Google OAuth settings:"
echo "    https://$APP_NAME.herokuapp.com/callback/google"

# Open the app
echo -n "🔓 Open the app in browser? (y/N): "
read OPEN_APP
if [ "$OPEN_APP" = "y" ] || [ "$OPEN_APP" = "Y" ]; then
    heroku open -a $APP_NAME
fi

echo "🎉 Deployment script completed!"
