# Dokploy Deployment Guide

## Environment Variables Configuration

To fix the OpenAI API key error in your deployed application, you need to set the following environment variables in your Dokploy panel:

### Required Environment Variables

1. **Django Settings**

   ```
   SECRET_KEY=your-secret-key-here
   DEBUG=True
   ALLOWED_HOSTS=localhost,127.0.0.1,dev.ai-sahakar.net,www.ai-sahakar.net,ai-sahakar.net
   ```

2. **Database Configuration**

   ```
   DB_ENGINE=django.db.backends.sqlite3
   DB_NAME=db.sqlite3
   ```

3. **API Keys** (CRITICAL - This fixes the 401 error)

   ```
   OPENAI_API_KEY=your-openai-api-key-here
   ```

4. **CORS and CSRF Settings**

   ```
   CORS_ALLOWED_ORIGINS=https://dev.ai-sahakar.net,https://mum-01.ai-sahakar.net,http://localhost:8000,http://127.0.0.1:8000
   CSRF_TRUSTED_ORIGINS=https://dev.ai-sahakar.net,https://mum-01.ai-sahakar.net,http://localhost:8000,http://127.0.0.1:8000
   ```

5. **Optional Settings**
   ```
   REDIS_URL=redis://localhost:6379/1
   SENTRY_DSN=
   MAX_FILE_SIZE=10485760
   ```

## Steps to Fix the Deployment

1. **Go to your Dokploy panel**
2. **Navigate to your application settings**
3. **Find the "Environment Variables" section**
4. **Add each variable above with its corresponding value**
5. **Make sure to set the OPENAI_API_KEY with the correct value**
6. **Save the configuration**
7. **Redeploy the application**

## Important Notes

- The `OPENAI_API_KEY` is the most critical variable to fix the 401 error
- Make sure there are no extra spaces or quotes around the values
- The `DEBUG=True` setting is for development - set to `False` in production
- The `ALLOWED_HOSTS` should include your domain names

## Verification

After setting the environment variables and redeploying:

1. Check the application logs for any errors
2. Test the search functionality to ensure OpenAI API is working
3. Verify that static files are loading correctly
4. Test the login functionality

## Current Working Credentials

- **Admin**: admin / admin123
- **User**: SnehalAbhivant / Snehal@123
