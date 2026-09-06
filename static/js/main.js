// SpamProtection - Main JavaScript
// NOTE: Text-analysis logic (handleTextAnalysis, displayAnalysisResults,
// createLoadingSpinner, createErrorMessage, getRiskColorInfo) lives in
// dashboard.html and is intentionally NOT duplicated here to avoid conflicting
// implementations and double event bindings.

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('SpamProtection initialized');

    // Initialize tooltips
    initializeTooltips();

    // Initialize animations
    initializeAnimations();

    // Initialize form handlers
    initializeFormHandlers();

    // Initialize auto-refresh
    initializeAutoRefresh();
});

/**
 * Initialize Bootstrap tooltips
 */
function initializeTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

/**
 * Initialize scroll animations
 */
function initializeAnimations() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in-up');
            }
        });
    }, {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    });

    // Observe all cards and feature elements
    document.querySelectorAll('.card, .feature-card, .step-card').forEach(el => {
        observer.observe(el);
    });
}

/**
 * Initialize form handlers.
 * Generic loading-state binding for normal forms. The #textAnalysisForm is
 * handled explicitly by dashboard.html and is intentionally excluded here to
 * prevent double submission.
 */
function initializeFormHandlers() {
    document.querySelectorAll('form:not(#textAnalysisForm)').forEach(form => {
        form.addEventListener('submit', function() {
            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) {
                addLoadingState(submitBtn);
            }
        });
    });
}

/**
 * Initialize auto-refresh for results page
 */
function initializeAutoRefresh() {
    const refreshBtn = document.querySelector('[href*="analyze_emails"]');
    if (refreshBtn && window.location.pathname.includes('analyze')) {
        // Add refresh functionality
        refreshBtn.addEventListener('click', function(e) {
            addLoadingState(this);
        });
    }
}

/**
 * Add loading state to button
 */
function addLoadingState(button) {
    if (!button || button.dataset.originalText) return;
    const originalText = button.innerHTML;
    button.dataset.originalText = originalText;
    button.disabled = true;
    button.innerHTML = `
        <span class="spinner-border spinner-border-sm me-2" role="status"></span>
        Loading...
    `;
}

/**
 * Remove loading state from button
 */
function removeLoadingState(button) {
    if (!button) return;
    button.disabled = false;
    button.innerHTML = button.dataset.originalText || button.innerHTML;
    delete button.dataset.originalText;
}

/**
 * Create loading spinner HTML
 */
function createLoadingSpinner(text = 'Loading...') {
    return `
        <div class="text-center py-4">
            <div class="spinner-border text-primary mb-3" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="text-muted">${text}</p>
        </div>
    `;
}

/**
 * Create error message HTML
 */
function createErrorMessage(message) {
    return `
        <div class="alert alert-danger" role="alert">
            <i class="fas fa-exclamation-triangle me-2"></i>
            <strong>Error:</strong> ${message}
        </div>
    `;
}

/**
 * Show alert message
 */
function showAlert(message, type = 'info') {
    const alertContainer = document.querySelector('.container');
    if (!alertContainer) return;
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        <i class="fas fa-info-circle me-2"></i>
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    alertContainer.insertBefore(alertDiv, alertContainer.firstChild);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        if (alertDiv.parentNode) alertDiv.remove();
    }, 5000);
}

/**
 * Copy text to clipboard
 */
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showAlert('Copied to clipboard!', 'success');
    }).catch(err => {
        console.error('Failed to copy: ', err);
        showAlert('Failed to copy to clipboard', 'warning');
    });
}

/**
 * Format date for display
 */
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Global error handler.
 * Logs to console only (no UI alert spam) so a single error cannot stack
 * multiple dismissible alerts over the page.
 */
let _globalErrorLogged = false;
window.addEventListener('error', function(e) {
    console.error('Global error:', e.error || e.message);
    _globalErrorLogged = true;
});
