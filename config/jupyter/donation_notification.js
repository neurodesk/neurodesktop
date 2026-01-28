// Neurodesk donation notification
// This script runs when JupyterLab loads
(function() {
    'use strict';
    console.log('[Neurodesk] Donation notification script loaded');
    
    function showNotification() {
        console.log('[Neurodesk] Attempting to show notification');
        
        // Check if already shown in this session
        if (sessionStorage.getItem('neurodesk_donation_shown')) {
            console.log('[Neurodesk] Notification already shown this session');
            return;
        }
        
        // Create toast notification
        var toast = document.createElement('div');
        toast.id = 'neurodesk-donation-toast';
        toast.style.cssText = [
            'position: fixed',
            'top: 60px',
            'right: 20px',
            'background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            'color: white',
            'padding: 16px 20px',
            'border-radius: 8px',
            'box-shadow: 0 4px 12px rgba(0,0,0,0.3)',
            'z-index: 10001',
            'max-width: 400px',
            'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            'font-size: 14px',
            'line-height: 1.5',
            'animation: slideIn 0.3s ease-out'
        ].join('; ');
        
        // Add CSS animation
        var style = document.createElement('style');
        style.textContent = '@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }';
        document.head.appendChild(style);
        
        toast.innerHTML = [
            '<div style="display: flex; align-items: start; gap: 12px;">',
            '  <div style="font-size: 24px;">❤️</div>',
            '  <div style="flex: 1;">',
            '    <div style="font-weight: 600; margin-bottom: 8px;">Support Neurodesk!</div>',
            '    <div>Help us maintain this free platform by <a href="https://donations.uq.edu.au/EAINNEUR" target="_blank" rel="noopener" style="color: white; text-decoration: underline;">donating to our infrastructure costs</a>.</div>',
            '  </div>',
            '  <button id="neurodesk-dismiss" style="background: rgba(255,255,255,0.2); border: none; color: white; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 18px; line-height: 1;">✕</button>',
            '</div>'
        ].join('');
        
        document.body.appendChild(toast);
        console.log('[Neurodesk] Notification toast added to DOM');
        
        // Mark as shown
        sessionStorage.setItem('neurodesk_donation_shown', 'true');
        
        // Add dismiss handler
        var dismissBtn = document.getElementById('neurodesk-dismiss');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', function() {
                toast.style.transition = 'opacity 0.3s, transform 0.3s';
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(100%)';
                setTimeout(function() {
                    if (toast.parentNode) {
                        toast.remove();
                    }
                }, 300);
            });
        }
        
        // Auto-dismiss after 30 seconds
        setTimeout(function() {
            if (toast && toast.parentNode) {
                toast.style.transition = 'opacity 0.3s, transform 0.3s';
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(100%)';
                setTimeout(function() {
                    if (toast.parentNode) {
                        toast.remove();
                    }
                }, 300);
            }
        }, 30000);
    }
    
    // Wait for page to be ready and JupyterLab to initialize
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(showNotification, 3000);
        });
    } else {
        setTimeout(showNotification, 3000);
    }
})();
