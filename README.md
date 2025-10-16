# AI-Powered Document Search System
## CC & RCS Maharashtra - Search Utility

[![Django](https://img.shields.io/badge/Django-5.2.6-green.svg)](https://www.djangoproject.com/)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![HTML5](https://img.shields.io/badge/HTML5-Ready-orange.svg)](https://developer.mozilla.org/en-US/docs/Web/Guide/HTML/HTML5)
[![CSS3](https://img.shields.io/badge/CSS3-Modern-blue.svg)](https://developer.mozilla.org/en-US/docs/Web/CSS)
[![JavaScript](https://img.shields.io/badge/JavaScript-ES6+-yellow.svg)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![License](https://img.shields.io/badge/License-Government-red.svg)](LICENSE)

> **Advanced AI-powered document search and management system for the Commissioner for Cooperation and Registrar, Cooperative Societies, Maharashtra State.**

## 🎯 Overview

This Django-based web application provides intelligent document search capabilities using OpenAI's GPT models. It's specifically designed for the Maharashtra government's cooperative societies department to efficiently search, analyze, and manage PDF documents with AI-powered natural language queries.

### 🌟 Key Features

- **🤖 AI-Powered Search**: Natural language queries using OpenAI GPT-4
- **📄 PDF Processing**: Automatic text extraction and OCR capabilities
- **🔍 Advanced Search**: Full-text search with PostgreSQL integration
- **👥 User Management**: Role-based access control with department assignments
- **📁 File Organization**: Hierarchical folder structure for document management
- **🌐 Multi-language**: Support for English and Marathi
- **🔒 Secure**: CSRF protection, HTTPS support, and environment-based configuration
- **📱 Responsive**: Mobile-friendly interface with modern UI
- **🎨 Futuristic Landing Pages**: Beautiful animated static pages for coming soon and maintenance

## 🏗️ Architecture

```
searchutility/
├── flowdocs/                    # Main Django project
│   ├── flowdocs/               # Project settings
│   │   ├── settings.py         # Development settings
│   │   ├── settings_production.py  # Production settings
│   │   ├── wsgi.py            # WSGI configuration
│   │   └── urls.py            # Main URL routing
│   ├── core/                   # Core application
│   │   ├── models.py          # Database models
│   │   ├── views.py           # Application views
│   │   ├── utils.py           # AI utilities & PDF processing
│   │   ├── forms.py           # Django forms
│   │   ├── templates/         # HTML templates
│   │   └── static/            # Static assets
│   ├── media/                  # Uploaded files
│   ├── locale/                 # Internationalization
│   └── manage.py              # Django management
├── assets/                     # Futuristic landing pages assets
│   ├── css/
│   │   └── style.css          # Futuristic CSS with animations
│   ├── js/
│   │   └── main.js            # Interactive JavaScript
│   └── images/                # Maharashtra government assets
├── index.html                  # Main landing page with navigation
├── coming-soon.html           # Futuristic "Coming Soon" page
├── maintenance.html           # Animated "Under Maintenance" page
├── Dockerfile                  # Container configuration
├── start.sh                   # Container startup script
├── requirements.txt           # Python dependencies
├── .env.template              # Environment variables template
└── README.md                  # This file
```

## 🎨 Landing Pages

### Futuristic Static Pages

The root directory contains beautifully designed, animated static pages perfect for:

- **🚀 Coming Soon Page**: Pre-launch showcase with progress indicators
- **🔧 Maintenance Page**: System maintenance with live countdown timer
- **🏠 Index Page**: Navigation hub for administrators

#### Design Features:
- **Dark Theme**: Space-inspired color scheme with neon accents
- **Animated Backgrounds**: Dynamic gradients and floating particles
- **Glassmorphism**: Frosted glass effects with backdrop blur
- **Interactive Elements**: Hover effects and 3D transformations
- **Responsive Design**: Perfect on all devices

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 12+ (for production)
- Redis (for caching)
- Tesseract OCR
- Poppler utilities
- OpenAI API key

### Local Development

1. **Clone the repository**
   ```bash
   git clone https://github.com/Nimble-esolutions/searchutility.git
   cd searchutility
   ```

2. **Set up Python environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.template .env
   # Edit .env with your configuration
   ```

4. **Run database migrations**
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

5. **Start development server**
   ```bash
   python manage.py runserver
   ```

Visit `http://localhost:8000` to access the application.

## 🚀 Dokploy Deployment Guide

### Option 1: Static Build Type (Recommended for Landing Pages)

**Perfect for the futuristic landing pages!**

#### Step 1: Dokploy Configuration
- **Application Name**: `maharashtra-landing-pages`
- **Build Type**: `Static` ✅
- **Repository**: `Nimble-esolutions/searchutility`
- **Branch**: `development`

#### Step 2: Domain Configuration
- **Host**: `dev.ai-sahakar.net` (or your domain)
- **Path**: `/`
- **Internal Path**: `/`
- **Container Port**: `80`
- **HTTPS**: Enable with Let's Encrypt

#### Step 3: Deploy
Dokploy will automatically:
1. Copy all files from root directory to `/usr/share/nginx/html`
2. Use optimized Nginx Dockerfile
3. Serve the futuristic landing pages

**Result**: Your beautiful animated landing pages will be live! 🎉

### Option 2: Dockerfile Build Type (For Full Django App)

**For the complete AI-powered search system:**

#### Step 1: Dokploy Configuration
- **Application Name**: `cc-rcs-search-system`
- **Build Type**: `Dockerfile` ✅
- **Repository**: `Nimble-esolutions/searchutility`
- **Branch**: `development`
- **Dockerfile Path**: `./Dockerfile`

#### Step 2: Environment Variables
```bash
# Django Settings
SECRET_KEY=your-super-secret-key-here
DEBUG=False
ALLOWED_HOSTS=dev.ai-sahakar.net,www.ai-sahakar.net

# Database Configuration
DB_NAME=flowdocs
DB_USER=postgres
DB_PASSWORD=your-secure-db-password
DB_HOST=localhost
DB_PORT=5432

# OpenAI API Configuration
OPENAI_API_KEY=sk-proj-your-openai-api-key-here

# CORS and CSRF Configuration
CORS_ALLOWED_ORIGINS=https://dev.ai-sahakar.net,https://www.ai-sahakar.net
CSRF_TRUSTED_ORIGINS=https://dev.ai-sahakar.net,https://www.ai-sahakar.net
```

#### Step 3: Domain Configuration
- **Host**: `dev.ai-sahakar.net`
- **Container Port**: `8000`
- **HTTPS**: Enable with Let's Encrypt

## 🎨 Landing Pages Features

### Visual Effects:
- **Animated Backgrounds**: Multi-layer gradient animations
- **Particle Systems**: Floating geometric particles
- **Glow Effects**: Neon-style lighting on key elements
- **Shimmer Animations**: Subtle light sweeps across surfaces
- **3D Transforms**: Hover effects with depth and perspective

### Interactive Elements:
- **Hover Animations**: Smooth transitions on user interaction
- **Click Effects**: Tactile feedback on button presses
- **Progress Animations**: Animated progress bars and counters
- **Real-time Updates**: Live countdown and status updates

### Responsive Design:
- **Mobile-First**: Optimized for all screen sizes
- **Flexible Grids**: Adaptive layouts for different devices
- **Touch-Friendly**: Large touch targets for mobile users
- **Performance**: Optimized animations for smooth performance

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SECRET_KEY` | Django secret key | - | ✅ |
| `DEBUG` | Debug mode | `False` | ✅ |
| `ALLOWED_HOSTS` | Allowed hostnames | `localhost` | ✅ |
| `DB_NAME` | Database name | `flowdocs` | ✅ |
| `DB_USER` | Database user | `postgres` | ✅ |
| `DB_PASSWORD` | Database password | - | ✅ |
| `DB_HOST` | Database host | `localhost` | ✅ |
| `DB_PORT` | Database port | `5432` | ✅ |
| `OPENAI_API_KEY` | OpenAI API key | - | ✅ |
| `REDIS_URL` | Redis connection URL | - | ❌ |
| `SENTRY_DSN` | Sentry error tracking | - | ❌ |
| `CORS_ALLOWED_ORIGINS` | CORS allowed origins | - | ✅ |
| `CSRF_TRUSTED_ORIGINS` | CSRF trusted origins | - | ✅ |

## 📊 Database Models

### Core Models

- **`CustomUser`**: Extended user model with department and role fields
- **`Folder`**: Hierarchical folder structure for document organization
- **`PDFFile`**: PDF document metadata with search vectors and content
- **`SearchHistory`**: User search query tracking

### Key Features

- **Full-text search** with PostgreSQL's built-in search capabilities
- **File upload validation** with size and type restrictions
- **User role management** with department-based access control
- **Search vector indexing** for fast text search

## 🤖 AI Integration

### OpenAI GPT-4 Integration

The application uses OpenAI's GPT-4 model for:
- **Natural language queries**: Convert user questions to search results
- **Document summarization**: Generate summaries of PDF content
- **Context-aware responses**: Provide relevant answers based on document content

### PDF Processing Pipeline

1. **Upload**: User uploads PDF file
2. **Text Extraction**: Extract text using `pdf2image` and `pytesseract`
3. **Content Analysis**: Process text for search indexing
4. **AI Processing**: Generate embeddings and summaries
5. **Storage**: Store in database with search vectors

## 🔒 Security Features

- **CSRF Protection**: Cross-site request forgery protection
- **CORS Configuration**: Cross-origin resource sharing controls
- **HTTPS Enforcement**: SSL/TLS encryption in production
- **Environment-based Secrets**: Secure configuration management
- **User Authentication**: Django's built-in authentication system
- **Role-based Access**: Department and role-based permissions

## 🚨 Troubleshooting

### Common Issues

1. **Module Import Errors**
   ```bash
   # Ensure you're in the correct directory
   cd flowdocs
   python manage.py runserver
   ```

2. **Database Connection Issues**
   ```bash
   # Check database configuration
   python manage.py dbshell
   ```

3. **OpenAI API Errors**
   ```bash
   # Verify API key is set
   echo $OPENAI_API_KEY
   ```

4. **Static Files Not Loading**
   ```bash
   # Collect static files
   python manage.py collectstatic --noinput
   ```

### Logs and Monitoring

- **Application Logs**: Check Dokploy logs tab
- **Database Logs**: Monitor PostgreSQL logs
- **Error Tracking**: Use Sentry for production error monitoring

## 🔄 Updates and Maintenance

### Deployment Updates

1. **Code Updates**
   ```bash
   git push origin development
   # Dokploy will automatically redeploy
   ```

2. **Database Migrations**
   ```bash
   # Migrations run automatically during deployment
   # Manual migration: python manage.py migrate
   ```

3. **Environment Updates**
   - Update environment variables in Dokploy
   - Restart application if needed

### Backup Strategy

- **Database**: Regular PostgreSQL backups
- **Media Files**: Volume backup of `/app/flowdocs/media`
- **Configuration**: Environment variables backup

## 📞 Support

### Technical Support
- **Email**: support@ccrs.maharashtra.gov.in
- **Phone**: +91-20-XXXX-XXXX
- **Address**: Pune, Maharashtra, India

### Development Team
- **Repository**: [GitHub - Nimble-esolutions/searchutility](https://github.com/Nimble-esolutions/searchutility)
- **Issues**: Report bugs and feature requests on GitHub

## 📄 License

© 2025 Commissioner for Cooperation and Registrar, Cooperative Societies, Maharashtra State. All rights reserved.

---

**Note**: This application is specifically designed for the Maharashtra government's cooperative societies department. The futuristic landing pages are now in the root directory and ready for Dokploy Static deployment!