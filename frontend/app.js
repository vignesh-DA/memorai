// API Configuration
const API_BASE = 'http://localhost:8000/api/v1';

// Get auth token
function getAuthToken() {
    return localStorage.getItem('access_token');
}

// Get auth headers
function getAuthHeaders() {
    const token = getAuthToken();
    if (!token) {
        console.error('No access token found!');
        return {
            'Content-Type': 'application/json'
        };
    }
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

// Check if user is authenticated
function checkAuth() {
    if (!getAuthToken()) {
        window.location.href = 'auth.html';
        return false;
    }
    return true;
}

// Logout function
function logout() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user_email');
    window.location.href = 'auth.html';
}

// State Management
let state = {
    turnNumber: 0,
    isLoading: false,
    userEmail: localStorage.getItem('user_email') || 'User',
    totalProcessingTime: 0,
    messageCount: 0
};

// DOM Elements
const elements = {
    messageInput: document.getElementById('messageInput'),
    sendBtn: document.getElementById('sendBtn'),
    sendBtnText: document.getElementById('sendBtnText'),
    chatMessages: document.getElementById('chatMessages'),
    userId: document.getElementById('userId'),
    turnCount: document.getElementById('turnCount'),
    memoryCount: document.getElementById('memoryCount'),
    memoryBadge: document.getElementById('memoryBadge'),
    avgProcessingTime: document.getElementById('avgProcessingTime'),
    searchPanel: document.getElementById('searchPanel'),
    searchInput: document.getElementById('searchInput'),
    searchResults: document.getElementById('searchResults'),
    newChatBtn: document.getElementById('newChatBtn'),
    toggleSearchBtn: document.getElementById('toggleSearchBtn'),
    closeSearchBtn: document.getElementById('closeSearchBtn'),
    searchBtn: document.getElementById('searchBtn')
};

// Initialize App
function init() {
    // Check authentication first
    if (!checkAuth()) return;
    
    setupEventListeners();
    validateToken();  // Validate token before making other requests
    checkAPIHealth();
    loadStats();
    
    // Display user email
    const userDisplay = document.getElementById('userId');
    if (userDisplay) {
        userDisplay.textContent = state.userEmail;
        userDisplay.removeAttribute('contenteditable');
    }
}

// Validate authentication token
async function validateToken() {
    try {
        const response = await fetch(`${API_BASE}/auth/me`, {
            headers: getAuthHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Token validation failed');
        }
        
        const user = await response.json();
        state.userEmail = user.email;
        
        // Update display
        const userDisplay = document.getElementById('userId');
        if (userDisplay) {
            userDisplay.textContent = user.email;
        }
    } catch (error) {
        console.error('Token validation failed:', error);
        // Clear invalid token and redirect to login
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        window.location.href = 'auth.html';
    }
}

// Event Listeners
function setupEventListeners() {
    // Auto-resize textarea
    elements.messageInput.addEventListener('input', handleTextareaResize);
    
    // Send message
    elements.sendBtn.addEventListener('click', sendMessage);
    elements.messageInput.addEventListener('keydown', handleKeyPress);
    
    // Search
    elements.toggleSearchBtn.addEventListener('click', toggleSearch);
    elements.closeSearchBtn.addEventListener('click', toggleSearch);
    elements.searchBtn.addEventListener('click', searchMemories);
    elements.searchInput.addEventListener('keypress', handleSearchKeyPress);
    
    // New chat
    elements.newChatBtn.addEventListener('click', newChat);
    
    // Logout button
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', logout);
    }
}

// Textarea Auto-resize
function handleTextareaResize() {
    elements.messageInput.style.height = 'auto';
    elements.messageInput.style.height = elements.messageInput.scrollHeight + 'px';
}

// Key Press Handler
function handleKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// Search Key Press Handler
function handleSearchKeyPress(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        searchMemories();
    }
}

// User ID Change Handler
function handleUserIdChange() {
    state.userId = elements.userId.value.trim();
    loadStats();
}

// Send Message
async function sendMessage() {
    if (state.isLoading) return;

    const message = elements.messageInput.value.trim();
    
    if (!message) {
        showToast('Please enter a message', 'error');
        return;
    }

    state.isLoading = true;
    elements.sendBtn.disabled = true;
    elements.sendBtnText.textContent = 'Sending...';

    // Clear input
    elements.messageInput.value = '';
    elements.messageInput.style.height = 'auto';

    // Remove empty state
    const emptyState = elements.chatMessages.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Add user message
    addMessage('user', message, state.turnNumber);
    state.turnNumber++;

    // Show loading
    showLoading();

    try {
        const response = await fetch(`${API_BASE}/conversation`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                turn_number: state.turnNumber,
                message: message,
                include_memories: true
            })
        });

        hideLoading();

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const errorMessage = errorData.detail || `HTTP error! status: ${response.status}`;
            
            // If unauthorized, redirect to login
            if (response.status === 401) {
                showToast('Session expired. Please log in again.', 'error');
                setTimeout(() => {
                    localStorage.removeItem('access_token');
                    localStorage.removeItem('refresh_token');
                    window.location.href = 'auth.html';
                }, 2000);
                return;
            }
            
            throw new Error(errorMessage);
        }

        const data = await response.json();
        
        // Add assistant response
        addMessage('assistant', data.response, state.turnNumber, {
            memories_used: data.memories_used.length,
            processing_time: data.processing_time_ms
        });

        // Update stats
        state.totalProcessingTime += data.processing_time_ms;
        state.messageCount++;
        updateAverageProcessingTime();
        
        await loadStats();
        showToast('Message sent successfully', 'success');

    } catch (error) {
        hideLoading();
        console.error('Error:', error);
        addMessage('error', `Error: ${error.message}`, state.turnNumber);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        state.isLoading = false;
        elements.sendBtn.disabled = false;
        elements.sendBtnText.textContent = 'Send';
    }
}

// Add Message to Chat
function addMessage(type, content, turn, meta = {}) {
    const messageWrapper = document.createElement('div');
    messageWrapper.className = `message-wrapper ${type}`;
    
    let headerText = type === 'user' ? 'You' : 'Assistant';
    let metaHtml = '';
    
    if (meta.memories_used !== undefined) {
        metaHtml = `<span class="message-meta">${meta.memories_used} memories • ${Math.round(meta.processing_time)}ms</span>`;
    }
    
    if (type === 'error') {
        messageWrapper.style.background = '#fee';
        messageWrapper.style.padding = '12px';
        messageWrapper.style.borderRadius = '8px';
        messageWrapper.style.border = '1px solid #fcc';
    }
    
    messageWrapper.innerHTML = `
        <div class="message-header ${type}">
            ${headerText}
            ${metaHtml}
        </div>
        <div class="message-content">${escapeHtml(content)}</div>
    `;
    
    elements.chatMessages.appendChild(messageWrapper);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

// Show Loading Indicator
function showLoading() {
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'loadingIndicator';
    loadingDiv.className = 'loading-indicator';
    loadingDiv.innerHTML = `
        <div class="loading-dots">
            <span></span>
            <span></span>
            <span></span>
        </div>
        <span>Thinking...</span>
    `;
    elements.chatMessages.appendChild(loadingDiv);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

// Hide Loading Indicator
function hideLoading() {
    const loading = document.getElementById('loadingIndicator');
    if (loading) loading.remove();
}

// Load Statistics
async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/memories/stats`, {
            headers: getAuthHeaders()
        });
        if (response.ok) {
            const stats = await response.json();
            elements.memoryCount.textContent = stats.total_memories;
            elements.memoryBadge.textContent = `${stats.total_memories} memories`;
        }
        elements.turnCount.textContent = state.turnNumber;
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// Update Average Processing Time
function updateAverageProcessingTime() {
    if (state.messageCount > 0) {
        const avg = Math.round(state.totalProcessingTime / state.messageCount);
        elements.avgProcessingTime.textContent = `${avg}ms`;
    }
}

// Toggle Search Panel
function toggleSearch() {
    elements.searchPanel.classList.toggle('open');
}

// Search Memories
async function searchMemories() {
    const query = elements.searchInput.value.trim();
    
    if (!query) {
        showToast('Please enter a search query', 'error');
        return;
    }

    elements.searchResults.innerHTML = '<div class="loading-indicator"><span>Searching...</span></div>';

    try {
        const response = await fetch(`${API_BASE}/memories/search`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                query: query,
                top_k: 10
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const results = await response.json();
        
        if (results.length === 0) {
            elements.searchResults.innerHTML = '<div class="empty-state" style="padding: 20px;"><p style="font-size: 13px;">No memories found</p></div>';
            return;
        }

        elements.searchResults.innerHTML = results.map(result => `
            <div class="memory-card">
                <span class="memory-type">${result.memory.type}</span>
                <div class="memory-content">${escapeHtml(result.memory.content)}</div>
                <div class="memory-meta">
                    Relevance: ${(result.relevance_score * 100).toFixed(0)}% • 
                    Confidence: ${(result.memory.metadata.confidence * 100).toFixed(0)}% •
                    Turn ${result.memory.metadata.source_turn}
                </div>
            </div>
        `).join('');

        showToast(`Found ${results.length} memories`, 'success');

    } catch (error) {
        console.error('Error:', error);
        elements.searchResults.innerHTML = `<div class="empty-state" style="padding: 20px;"><p style="font-size: 13px; color: #ef4444;">Error: ${error.message}</p></div>`;
        showToast(`Search failed: ${error.message}`, 'error');
    }
}

// New Chat
function newChat() {
    if (confirm('Start a new chat? This will clear the current conversation view (memories are preserved).')) {
        elements.chatMessages.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">💬</div>
                <h3>Start a conversation</h3>
                <p>Your messages will be remembered across thousands of conversation turns. Try asking about past topics!</p>
            </div>
        `;
        state.turnNumber = 0;
        state.totalProcessingTime = 0;
        state.messageCount = 0;
        elements.avgProcessingTime.textContent = '0ms';
        loadStats();
        showToast('New chat started', 'info');
    }
}

// Show Toast Notification
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    
    const container = document.getElementById('toastContainer');
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Check API Health
async function checkAPIHealth() {
    try {
        const response = await fetch('http://localhost:8000/api/health');
        if (response.ok) {
            showToast('Connected to Memory AI', 'success');
        } else {
            showToast('⚠️ API server is not responding', 'error');
        }
    } catch (error) {
        showToast('⚠️ Cannot connect to API server', 'error');
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
