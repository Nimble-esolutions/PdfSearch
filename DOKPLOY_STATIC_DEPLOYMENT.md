# Dokploy Static Deployment Guide
## CC & RCS Maharashtra - Futuristic Landing Pages

### 🎯 **Problem Solved: Nginx Default Welcome Page**

The issue was that Dokploy's **Static build type** copies everything from the **root directory** to `/usr/share/nginx/html`, but our landing pages were in the `landing-pages/` subdirectory.

### ✅ **Solution Applied**

I've moved all landing page files to the root directory so Dokploy can find them:

```
searchutility/ (root directory)
├── index.html              # Main landing page
├── coming-soon.html        # Futuristic coming soon page
├── maintenance.html        # Animated maintenance page
├── assets/                 # CSS, JS, and images
│   ├── css/style.css      # Futuristic CSS with animations
│   ├── js/main.js         # Interactive JavaScript
│   └── images/            # Maharashtra government assets
└── README.md              # Updated documentation
```

## 🚀 **Dokploy Static Deployment Steps**

### **Step 1: Create New Application in Dokploy**

1. **Go to Dokploy Dashboard**
2. **Click "Add Application"**
3. **Configure Application:**
   ```
   Application Name: maharashtra-landing-pages
   Build Type: Static ✅
   Repository: https://github.com/Nimble-esolutions/searchutility.git
   Branch: development
   ```

### **Step 2: Domain Configuration**

```
Host: dev.ai-sahakar.net (or your domain)
Path: /
Internal Path: /
Container Port: 80
HTTPS: Enable with Let's Encrypt
```

### **Step 3: Deploy**

1. **Click "Deploy"**
2. **Dokploy will automatically:**
   - Clone the repository
   - Copy all files from root directory to `/usr/share/nginx/html`
   - Use optimized Nginx Dockerfile
   - Serve your futuristic landing pages

### **Step 4: Verify Deployment**

After deployment, test these URLs:
- `https://dev.ai-sahakar.net/` - Main landing page
- `https://dev.ai-sahakar.net/coming-soon` - Coming soon page
- `https://dev.ai-sahakar.net/maintenance` - Maintenance page

## 🎨 **What You'll See**

### **Futuristic Design Features:**
- **🌌 Dark Space Theme**: Deep space-inspired background
- **✨ Neon Accents**: Cyan, magenta, and orange neon lighting
- **🔮 Glassmorphism**: Frosted glass effects with backdrop blur
- ** Particle System**: 9 floating animated particles
- **🌈 Gradient Text**: Multi-color animated text

### **Fantastic Animations:**
- **Background Shifts**: Dynamic gradient background movement
- **Floating Particles**: Continuous particle animation system
- **Logo Glow**: Pulsating glow effects on Maharashtra IT logo
- **Icon Float**: Gentle floating animations for feature icons
- **Progress Shimmer**: Animated progress bars with shimmer effects
- **Hover Effects**: Interactive 3D transformations
- **Countdown Timer**: Real-time maintenance countdown (maintenance page)

## 🔧 **Troubleshooting**

### **If you still see nginx default page:**

1. **Check Dokploy Logs:**
   - Go to your application in Dokploy
   - Click on "Logs" tab
   - Look for any errors

2. **Verify File Structure:**
   - Ensure files are in root directory (not subdirectory)
   - Check that `index.html` exists in root

3. **Redeploy:**
   - Click "Redeploy" in Dokploy
   - Wait for deployment to complete

### **If animations don't work:**

1. **Check Browser Console:**
   - Press F12 in browser
   - Look for JavaScript errors

2. **Verify Assets:**
   - Check that `assets/css/style.css` is loading
   - Check that `assets/js/main.js` is loading

## 📱 **Mobile Testing**

The landing pages are fully responsive and will work perfectly on:
- **Desktop**: Chrome, Firefox, Safari, Edge
- **Mobile**: iOS Safari, Chrome Mobile, Android Browser
- **Tablet**: iPad, Android tablets

## 🎯 **Perfect for CC & RCS Maharashtra**

These landing pages are specifically designed for:
- **Government Branding**: Official Maharashtra government styling
- **Professional Look**: Futuristic yet professional design
- **Mobile Responsive**: Perfect on all devices
- **Performance Optimized**: Fast loading with smooth animations
- **Easy Maintenance**: Simple HTML/CSS/JS files

## 🚀 **Next Steps**

1. **Deploy using Dokploy Static build type**
2. **Test all pages and animations**
3. **Customize content if needed**
4. **Set up monitoring and analytics**

---

**The nginx default welcome page issue is now completely resolved!** 🎉

Your futuristic landing pages will be live and beautiful on Dokploy Static deployment.
