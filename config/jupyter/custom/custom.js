// Donation Banner Script
(function() {
    'use strict';
    
    // Check if banner was previously closed
    const bannerClosed = localStorage.getItem('neurodeskDonationBannerClosed');
    if (bannerClosed) {
        return;
    }
    
    // Wait for DOM to be ready
    function addDonationBanner() {
        // Create banner element
        const banner = document.createElement('div');
        banner.id = 'donation-banner';
        banner.innerHTML = `
            <span class="heart-icon">❤️</span>
            <span>
                Support Neurodesk! Help us maintain this free platform by 
                <a href="https://donations.uq.edu.au/EAINNEUR" target="_blank" rel="noopener noreferrer">donating to our infrastructure costs</a>.
            </span>
            <button id="donation-banner-close">Dismiss</button>
        `;
        
        // Add banner to page
        document.body.insertBefore(banner, document.body.firstChild);
        
        // Add close functionality
        const closeButton = document.getElementById('donation-banner-close');
        closeButton.addEventListener('click', function() {
            banner.style.display = 'none';
            localStorage.setItem('neurodeskDonationBannerClosed', 'true');
        });
    }
    
    // Run when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', addDonationBanner);
    } else {
        addDonationBanner();
    }
})();
