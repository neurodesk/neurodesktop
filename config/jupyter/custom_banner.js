// Donation banner injection for JupyterLab
// This script will be injected into the JupyterLab page

(function() {
    'use strict';
    
    // Wait for the page to be fully loaded
    function injectBanner() {
        // Check if banner was previously closed
        let bannerClosed = false;
        try {
            bannerClosed = localStorage.getItem('neurodeskDonationBannerClosed') === 'true';
        } catch (e) {
            console.warn('localStorage not available for donation banner:', e);
        }
        
        if (bannerClosed) {
            return; // Don't show banner if previously dismissed
        }
        
        // Create banner element
        const banner = document.createElement('div');
        banner.id = 'donation-banner';
        banner.setAttribute('role', 'banner');
        banner.setAttribute('aria-label', 'Neurodesk donation request');
        banner.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 20px; text-align: center; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; font-size: 14px; z-index: 10000; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15); display: flex; align-items: center; justify-content: center; gap: 15px;';
        
        // Create heart icon
        const heartIcon = document.createElement('span');
        heartIcon.className = 'heart-icon';
        heartIcon.setAttribute('aria-hidden', 'true');
        heartIcon.style.cssText = 'display: inline-block; animation: heartbeat 1.5s ease-in-out infinite;';
        heartIcon.textContent = '❤️';
        
        // Create message
        const message = document.createElement('span');
        message.innerHTML = 'Support Neurodesk! Help us maintain this free platform by <a href="https://donations.uq.edu.au/EAINNEUR" target="_blank" rel="noopener noreferrer" style="color: white; font-weight: 600; text-decoration: underline;">donating to our infrastructure costs <span style="font-size: 0.85em;">(opens in new tab)</span></a>.';
        
        // Create dismiss button
        const closeButton = document.createElement('button');
        closeButton.id = 'donation-banner-close';
        closeButton.setAttribute('aria-label', 'Dismiss donation banner');
        closeButton.textContent = 'Dismiss';
        closeButton.style.cssText = 'background: rgba(255, 255, 255, 0.2); border: none; color: white; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; margin-left: 15px; transition: background 0.2s;';
        
        closeButton.addEventListener('mouseover', function() {
            this.style.background = 'rgba(255, 255, 255, 0.3)';
        });
        closeButton.addEventListener('mouseout', function() {
            this.style.background = 'rgba(255, 255, 255, 0.2)';
        });
        closeButton.addEventListener('focus', function() {
            this.style.outline = '2px solid white';
            this.style.outlineOffset = '2px';
        });
        closeButton.addEventListener('blur', function() {
            this.style.outline = 'none';
        });
        
        closeButton.addEventListener('click', function() {
            banner.style.display = 'none';
            try {
                localStorage.setItem('neurodeskDonationBannerClosed', 'true');
            } catch (e) {
                console.warn('Could not save banner dismissal:', e);
            }
        });
        
        // Assemble banner
        banner.appendChild(heartIcon);
        banner.appendChild(message);
        banner.appendChild(closeButton);
        
        // Add CSS animation
        const style = document.createElement('style');
        style.textContent = `
            @keyframes heartbeat {
                0%, 100% { transform: scale(1); }
                10%, 30% { transform: scale(1.1); }
                20%, 40% { transform: scale(1); }
            }
            body { padding-top: 50px !important; }
        `;
        document.head.appendChild(style);
        
        // Insert banner at the top of the body
        document.body.insertBefore(banner, document.body.firstChild);
    }
    
    // Try to inject immediately if DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectBanner);
    } else {
        // DOM is already loaded
        injectBanner();
    }
})();
