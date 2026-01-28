// Donation notification for JupyterLab
// This script creates a toast notification using JupyterLab's built-in notification system

(function() {
    'use strict';
    
    console.log('Loading donation notification...');
    
    // Check if already dismissed
    if (localStorage.getItem('neurodeskDonationDismissed') === 'true') {
        console.log('Donation notification already dismissed');
        return;
    }
    
    // Wait for JupyterLab to be fully ready
    function waitForJupyterLab(callback, maxAttempts = 50) {
        var attempts = 0;
        var interval = setInterval(function() {
            attempts++;
            if (window.jupyterapp || attempts >= maxAttempts) {
                clearInterval(interval);
                if (window.jupyterapp) {
                    console.log('JupyterLab found, showing notification');
                    callback();
                } else {
                    console.log('JupyterLab not found after maximum attempts');
                }
            }
        }, 200);
    }
    
    function showNotification() {
        try {
            // Create a toast-style notification
            var notification = document.createElement('div');
            notification.className = 'neurodesk-donation-notification';
            notification.setAttribute('role', 'alert');
            notification.setAttribute('aria-live', 'polite');
            
            notification.style.cssText = [
                'position: fixed',
                'top: 60px',
                'right: 20px',
                'background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                'color: white',
                'padding: 20px',
                'border-radius: 8px',
                'box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3)',
                'z-index: 10001',
                'max-width: 400px',
                'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
                'animation: slideIn 0.3s ease-out'
            ].join('; ');
            
            notification.innerHTML = [
                '<style>',
                '@keyframes slideIn {',
                '  from { transform: translateX(100%); opacity: 0; }',
                '  to { transform: translateX(0); opacity: 1; }',
                '}',
                '.neurodesk-donation-notification button:hover {',
                '  opacity: 0.9;',
                '}',
                '</style>',
                '<div style="display: flex; gap: 12px; align-items: flex-start;">',
                '  <div style="font-size: 28px; line-height: 1;">❤️</div>',
                '  <div style="flex: 1;">',
                '    <div style="font-size: 16px; font-weight: 600; margin-bottom: 8px;">Support Neurodesk!</div>',
                '    <div style="font-size: 14px; line-height: 1.5; margin-bottom: 12px;">',
                '      Help us maintain this free platform by donating to our infrastructure costs.',
                '    </div>',
                '    <div style="display: flex; gap: 8px;">',
                '      <a href="https://donations.uq.edu.au/EAINNEUR" target="_blank" rel="noopener noreferrer" ',
                '         style="background: white; color: #667eea; padding: 8px 16px; border-radius: 4px; text-decoration: none; font-weight: 600; font-size: 13px; display: inline-block;">',
                '        Donate',
                '      </a>',
                '      <button id="neurodesk-dismiss-donation" ',
                '              style="background: rgba(255, 255, 255, 0.2); color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 13px;">',
                '        Dismiss',
                '      </button>',
                '    </div>',
                '  </div>',
                '</div>'
            ].join('');
            
            document.body.appendChild(notification);
            console.log('Notification added to DOM');
            
            // Add dismiss functionality
            var dismissBtn = document.getElementById('neurodesk-dismiss-donation');
            if (dismissBtn) {
                dismissBtn.addEventListener('click', function() {
                    notification.style.animation = 'slideIn 0.3s ease-out reverse';
                    setTimeout(function() {
                        notification.remove();
                    }, 300);
                    try {
                        localStorage.setItem('neurodeskDonationDismissed', 'true');
                        console.log('Donation notification dismissed');
                    } catch (e) {
                        console.warn('Could not save dismissal to localStorage:', e);
                    }
                });
            }
            
            // Auto-dismiss after 30 seconds
            setTimeout(function() {
                if (notification.parentNode) {
                    notification.style.animation = 'slideIn 0.3s ease-out reverse';
                    setTimeout(function() {
                        if (notification.parentNode) {
                            notification.remove();
                        }
                    }, 300);
                }
            }, 30000);
            
        } catch (error) {
            console.error('Error showing donation notification:', error);
        }
    }
    
    // Start waiting for JupyterLab
    waitForJupyterLab(showNotification);
    
})();
