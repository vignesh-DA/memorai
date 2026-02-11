// API Configuration - Use dynamic origin for flexibility
const API_BASE = `${window.location.origin}/api/v1`;
const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // ms
const REQUEST_TIMEOUT = 30000; // 30s

// Abort controllers for request cancellation
const abortControllers = new Map();

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

// ======================
// PRODUCTION UTILITIES
// ======================

// Debounce utility
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Exponential backoff retry with timeout
async function fetchWithRetry(url, options = {}, retries = MAX_RETRIES) {
    const requestId = `${url}-${Date.now()}`;
    const controller = new AbortController();
    abortControllers.set(requestId, controller);
    
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
    
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        
        clearTimeout(timeout);
        abortControllers.delete(requestId);
        
        // If 401, redirect to login
        if (response.status === 401) {
            handleAuthError();
            throw new Error('Authentication required');
        }
        
        return response;
    } catch (error) {
        clearTimeout(timeout);
        abortControllers.delete(requestId);
        
        // Don't retry on abort or auth errors
        if (error.name === 'AbortError' || error.message === 'Authentication required') {
            throw error;
        }
        
        // Retry on network errors
        if (retries > 0 && (error.name === 'TypeError' || error.message.includes('fetch'))) {
            console.log(`Retry ${MAX_RETRIES - retries + 1}/${MAX_RETRIES} for ${url}`);
            await new Promise(resolve => setTimeout(resolve, RETRY_DELAY * (MAX_RETRIES - retries + 1)));
            return fetchWithRetry(url, options, retries - 1);
        }
        
        throw error;
    }
}

// Cancel pending request
function cancelRequest(requestId) {
    const controller = abortControllers.get(requestId);
    if (controller) {
        controller.abort();
        abortControllers.delete(requestId);
    }
}

// Detect offline mode
let isOnline = navigator.onLine;
window.addEventListener('online', () => {
    isOnline = true;
    showToast('Connection restored', 'success');
    // Retry failed requests
    loadStats();
    loadConversations();
});
window.addEventListener('offline', () => {
    isOnline = false;
    showToast('You are offline. Changes will sync when connection is restored.', 'warning');
});

// Global error handler
function handleAuthError() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user_email');
    showToast('Session expired. Redirecting to login...', 'error');
    setTimeout(() => {
        window.location.href = 'auth.html';
    }, 2000);
}

// Global error boundary
window.addEventListener('error', (event) => {
    console.error('Global error:', event.error);
    showGlobalError('An unexpected error occurred. Please refresh the page.');
});

window.addEventListener('unhandledrejection', (event) => {
    console.error('Unhandled promise rejection:', event.reason);
    showGlobalError('An unexpected error occurred. Please refresh the page.');
});

function showGlobalError(message) {
    const errorOverlay = document.createElement('div');
    errorOverlay.id = 'global-error-overlay';
    errorOverlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0,0,0,0.9);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;
    errorOverlay.innerHTML = `
        <div style="background: white; padding: 30px; border-radius: 12px; max-width: 500px; text-align: center;">
            <h2 style="color: #dc2626; margin-bottom: 16px;">⚠️ Error</h2>
            <p style="margin-bottom: 24px;">${message}</p>
            <button onclick="location.reload()" style="
                background: #2563eb;
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 16px;
            ">Reload Page</button>
        </div>
    `;
    document.body.appendChild(errorOverlay);
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
    isSending: false, // Prevent double-send
    userEmail: localStorage.getItem('user_email') || 'User',
    totalProcessingTime: 0,
    messageCount: 0,
    currentConversationId: null,
    conversations: [],
    conversationCache: new Map(), // Cache conversation data
    conversationPage: 1,
    conversationLimit: 20,
    hasMoreConversations: true,
    lastSearchQuery: '',
    currentSearchController: null
};

// DOM Elements
const elements = {
    messageInput: document.getElementById('messageInput'),
    sendBtn: document.getElementById('sendBtn'),
    sendBtnText: document.getElementById('sendBtnText'),
    chatMessages: document.getElementById('chatMessages'),
    userId: document.getElementById('userId'),
    turnCount: document.getElementById('turnCount'),  // May not exist
    memoryCount: document.getElementById('memoryCount'),  // May not exist
    memoryBadge: document.getElementById('memoryBadge'),
    avgProcessingTime: document.getElementById('avgProcessingTime'),  // May not exist
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
    setupInfiniteScroll(); // Setup infinite scroll for conversations
    validateToken();  // Validate token before making other requests
    checkAPIHealth();
    loadStats();
    loadConversations();  // Load conversation list
    
    // Display user email
    const userDisplay = document.getElementById('userId');
    if (userDisplay) {
        userDisplay.textContent = state.userEmail;
        userDisplay.removeAttribute('contenteditable');
    }
}

// Conversation Management Functions
async function loadConversations(append = false) {
    try {
        if (!append) {
            state.conversationPage = 1;
            state.hasMoreConversations = true;
        }
        
        const offset = (state.conversationPage - 1) * state.conversationLimit;
        const response = await fetchWithRetry(
            `${API_BASE}/conversations?limit=${state.conversationLimit}&offset=${offset}`,
            { headers: getAuthHeaders() }
        );

        if (!response.ok) {
            throw new Error('Failed to load conversations');
        }

        const data = await response.json();
        const newConversations = data.conversations || [];
        
        if (append) {
            state.conversations = [...state.conversations, ...newConversations];
        } else {
            state.conversations = newConversations;
        }
        
        // Check if there are more
        state.hasMoreConversations = newConversations.length === state.conversationLimit;
        
        renderConversations();
        
        // Auto-load the most recent conversation on initial load
        if (!append && !state.currentConversationId && state.conversations.length > 0) {
            const mostRecent = state.conversations[0];
            await switchConversation(mostRecent.conversation_id);
            console.log('Auto-loaded most recent conversation');
        }
    } catch (error) {
        console.error('Failed to load conversations:', error);
        if (error.message !== 'Authentication required') {
            showToast('Failed to load conversations', 'error');
        }
    }
}

// Load more conversations (infinite scroll)
async function loadMoreConversations() {
    if (!state.hasMoreConversations || state.isLoading) return;
    
    state.conversationPage++;
    await loadConversations(true);
}

// Setup infinite scroll for conversations
function setupInfiniteScroll() {
    const conversationsContainer = document.getElementById('conversations');
    if (!conversationsContainer) return;
    
    conversationsContainer.addEventListener('scroll', () => {
        const { scrollTop, scrollHeight, clientHeight } = conversationsContainer;
        if (scrollHeight - scrollTop <= clientHeight + 100) {
            loadMoreConversations();
        }
    });
}

async function createNewConversation() {
    try {
        const response = await fetch(`${API_BASE}/conversations`, {
            method: 'POST',
            headers: getAuthHeaders()
        });

        if (!response.ok) {
            throw new Error('Failed to create conversation');
        }

        const conversation = await response.json();
        state.currentConversationId = conversation.conversation_id;
        state.turnNumber = 0;
        
        // Clear chat
        elements.chatMessages.innerHTML = '';
        
        // Automatically send a greeting to trigger AI's personalized response
        showLoading();
        
        try {
            const greetingResponse = await fetch(`${API_BASE}/conversation`, {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify({
                    turn_number: 0,
                    message: "hi",
                    include_memories: true,
                    conversation_id: state.currentConversationId
                })
            });
            
            hideLoading();
            
            if (greetingResponse.ok) {
                const data = await greetingResponse.json();
                
                // DON'T show user's "hi" - let AI speak first for new chats
                // Only show AI's personalized greeting
                addMessage('assistant', data.response, 0, {
                    memories_used: data.memories_used.length,
                    processing_time: data.processing_time_ms
                });
                
                state.turnNumber = 1;
                
                // Update stats
                state.totalProcessingTime += data.processing_time_ms;
                state.messageCount++;
                updateAverageProcessingTime();
                await loadStats();
            }
        } catch (greetingError) {
            hideLoading();
            console.error('Failed to get greeting:', greetingError);
            // If greeting fails, just show empty state
            elements.chatMessages.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">💬</div>
                    <h3>Start a conversation</h3>
                    <p>Your messages will be remembered across thousands of conversation turns.</p>
                </div>
            `;
        }
        
        // Reload conversation list
        await loadConversations();
        
        showToast('New conversation started', 'success');
    } catch (error) {
        console.error('Failed to create conversation:', error);
        showToast('Failed to create new conversation', 'error');
    }
}

async function switchConversation(conversationId) {
    if (state.currentConversationId === conversationId) return;
    
    state.currentConversationId = conversationId;
    state.turnNumber = 0;
    
    // Check cache first (stale-while-revalidate pattern)
    if (state.conversationCache.has(conversationId)) {
        const cached = state.conversationCache.get(conversationId);
        renderConversationData(cached);
        renderConversations();
        // Revalidate in background
        revalidateConversation(conversationId);
        return;
    }
    
    // Clear chat
    elements.chatMessages.innerHTML = '<div class="loading">Loading conversation...</div>';
    
    try {
        // Load conversation turns with retry
        const exportResponse = await fetchWithRetry(
            `${API_BASE}/conversations/${conversationId}/export`,
            { headers: getAuthHeaders() }
        );
        
        if (!exportResponse.ok) {
            if (exportResponse.status === 404) {
                // Conversation not found - fallback to new one
                showToast('Conversation not found. Starting new chat.', 'warning');
                state.conversations = state.conversations.filter(c => c.conversation_id !== conversationId);
                await createNewConversation();
                return;
            }
            throw new Error('Failed to load conversation');
        }
        
        const exportData = await exportResponse.json();
        
        // Cache the conversation
        state.conversationCache.set(conversationId, exportData);
        
        // Render
        renderConversationData(exportData);
        
        // Update active state in UI
        renderConversations();
        
    } catch (error) {
        console.error('Failed to load conversation:', error);
        if (error.message !== 'Authentication required') {
            showToast('Failed to load conversation. Starting new chat.', 'error');
            await createNewConversation();
        }
    }
}

async function revalidateConversation(conversationId) {
    try {
        const response = await fetchWithRetry(
            `${API_BASE}/conversations/${conversationId}/export`,
            { headers: getAuthHeaders() }
        );
        if (response.ok) {
            const data = await response.json();
            state.conversationCache.set(conversationId, data);
        }
    } catch (error) {
        console.log('Background revalidation failed:', error);
    }
}

function renderConversationData(exportData) {
    elements.chatMessages.innerHTML = '';
    
    if (exportData.turns && exportData.turns.length > 0) {
        exportData.turns.forEach(turn => {
            addMessage('user', turn.user_message, turn.turn_number, false);
            if (turn.assistant_message) {
                addMessage('assistant', turn.assistant_message, turn.turn_number, false);
            }
        });
        
        state.turnNumber = exportData.turns[exportData.turns.length - 1].turn_number + 1;
    } else {
        elements.chatMessages.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">💬</div>
                <h3>Continue this conversation</h3>
                <p>Add a message to continue where you left off.</p>
            </div>
        `;
    }
    
    // Scroll to bottom
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

async function deleteConversation(conversationId, event) {
    event.stopPropagation();
    
    if (!confirm('Are you sure you want to delete this conversation? This cannot be undone.')) {
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Failed to delete conversation');
        }
        
        // If this is the current conversation, clear it
        if (state.currentConversationId === conversationId) {
            state.currentConversationId = null;
            state.turnNumber = 0;
            elements.chatMessages.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">💬</div>
                    <h3>Start a conversation</h3>
                    <p>Your messages will be remembered across thousands of conversation turns.</p>
                </div>
            `;
        }
        
        // Reload conversation list
        await loadConversations();
        
        showToast('Conversation deleted', 'success');
    } catch (error) {
        console.error('Failed to delete conversation:', error);
        showToast('Failed to delete conversation', 'error');
    }
}

async function archiveConversation(conversationId, event) {
    event.stopPropagation();
    
    try {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
            method: 'PATCH',
            headers: getAuthHeaders(),
            body: JSON.stringify({ is_archived: true })
        });
        
        if (!response.ok) {
            throw new Error('Failed to archive conversation');
        }
        
        await loadConversations();
        showToast('Conversation archived', 'success');
    } catch (error) {
        console.error('Failed to archive conversation:', error);
        showToast('Failed to archive conversation', 'error');
    }
}

async function exportConversation(conversationId, event) {
    event.stopPropagation();
    
    try {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}/export`, {
            headers: getAuthHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Failed to export conversation');
        }
        
        const data = await response.json();
        
        // Create and download JSON file
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `conversation_${conversationId}_${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
        
        showToast('Conversation exported', 'success');
    } catch (error) {
        console.error('Failed to export conversation:', error);
        showToast('Failed to export conversation', 'error');
    }
}

function renderConversations() {
    const container = document.getElementById('conversations');
    
    if (!state.conversations || state.conversations.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding: 20px; text-align: center;">
                <p style="font-size: 13px; color: #94a3b8;">No conversations yet</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = state.conversations.map(conv => {
        const date = new Date(conv.updated_at);
        const timeAgo = getTimeAgo(date);
        const isActive = state.currentConversationId === conv.conversation_id;
        
        return `
            <div class="conversation-item ${isActive ? 'active' : ''}" 
                 onclick="switchConversation('${conv.conversation_id}')">
                <div class="conversation-title">${conv.title || 'New Conversation'}</div>
                ${conv.last_message_preview ? `
                    <div class="conversation-preview">${conv.last_message_preview}</div>
                ` : ''}
                <div class="conversation-meta">
                    <span class="conversation-time">${timeAgo}</span>
                    <span class="conversation-turn-count">${conv.turn_count} turns</span>
                </div>
                <div class="conversation-actions">
                    <button class="conversation-action-btn" onclick="exportConversation('${conv.conversation_id}', event)">
                        Export
                    </button>
                    <button class="conversation-action-btn delete" onclick="deleteConversation('${conv.conversation_id}', event)">
                        Delete
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function getTimeAgo(date) {
    const seconds = Math.floor((new Date() - date) / 1000);
    
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    
    return date.toLocaleDateString();
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
    
    // Send message (debounce to prevent double-send on rapid clicks/enters)
    elements.sendBtn.addEventListener('click', sendMessage);
    elements.messageInput.addEventListener('keydown', handleKeyPress);
    
    // Search with real-time debouncing
    elements.toggleSearchBtn.addEventListener('click', toggleSearch);
    elements.closeSearchBtn.addEventListener('click', toggleSearch);
    elements.searchBtn.addEventListener('click', searchMemories);
    elements.searchInput.addEventListener('input', searchMemories); // Real-time debounced search
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
    // Double-send prevention
    if (state.isLoading || state.isSending) return;

    const message = elements.messageInput.value.trim();
    
    if (!message) {
        showToast('Please enter a message', 'error');
        return;
    }

    state.isLoading = true;
    state.isSending = true;
    elements.sendBtn.disabled = true;
    elements.sendBtnText.textContent = 'Sending...';

    // Clear input immediately
    elements.messageInput.value = '';
    elements.messageInput.style.height = 'auto';

    // Remove empty state
    const emptyState = elements.chatMessages.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Add user message
    addMessage('user', message, state.turnNumber);

    // Show loading
    showLoading();

    try {
        // Build request body
        const requestBody = {
            turn_number: state.turnNumber,
            message: message,
            include_memories: true
        };
        
        // Include conversation_id if we have one
        if (state.currentConversationId) {
            requestBody.conversation_id = state.currentConversationId;
        }
        
        const response = await fetchWithRetry(`${API_BASE}/conversation`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(requestBody)
        });

        hideLoading();

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const errorMessage = errorData.detail || `HTTP error! status: ${response.status}`;
            
            // If unauthorized, handled by fetchWithRetry
            if (response.status === 401) {
                return;
            }
            
            throw new Error(errorMessage);
        }

        const data = await response.json();
        
        // Update conversation ID if this was a new conversation
        if (!state.currentConversationId && data.conversation_id) {
            state.currentConversationId = data.conversation_id;
            // Invalidate cache and reload conversations
            state.conversationCache.clear();
            await loadConversations();
        } else if (state.currentConversationId) {
            // Invalidate cache for this conversation
            state.conversationCache.delete(state.currentConversationId);
        }
        
        // Increment turn number after successful response
        state.turnNumber++;
        
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
        state.isSending = false;
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
        const response = await fetchWithRetry(`${API_BASE}/memories/stats`, {
            headers: getAuthHeaders()
        });
        if (response.ok) {
            const stats = await response.json();
            if (elements.memoryCount) elements.memoryCount.textContent = stats.total_memories;
            if (elements.memoryBadge) elements.memoryBadge.textContent = `${stats.total_memories} memories`;
        }
        if (elements.turnCount) elements.turnCount.textContent = state.turnNumber;
    } catch (error) {
        console.error('Error loading stats:', error);
        // Don't show error to user - it's not critical
    }
}

// Update Average Processing Time
function updateAverageProcessingTime() {
    if (state.messageCount > 0 && elements.avgProcessingTime) {
        const avg = Math.round(state.totalProcessingTime / state.messageCount);
        elements.avgProcessingTime.textContent = `${avg}ms`;
    }
}

// Toggle Search Panel
function toggleSearch() {
    elements.searchPanel.classList.toggle('open');
}

// Search Memories
// Search with debouncing and cancellation
const debouncedSearch = debounce(performSearch, 500);

async function searchMemories() {
    const query = elements.searchInput.value.trim();
    
    if (!query) {
        elements.searchResults.innerHTML = '';
        return;
    }

    // Cancel previous search
    if (state.currentSearchController) {
        state.currentSearchController.abort();
    }

    state.lastSearchQuery = query;
    elements.searchResults.innerHTML = '<div class="loading-indicator"><span>Searching...</span></div>';
    
    debouncedSearch(query);
}

async function performSearch(query) {
    // Double-check query hasn't changed
    if (query !== state.lastSearchQuery) return;

    try {
        state.currentSearchController = new AbortController();
        
        const response = await fetch(`${API_BASE}/memories/search`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                query: query,
                top_k: 10
            }),
            signal: state.currentSearchController.signal
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const results = await response.json();
        
        // Check query hasn't changed during request
        if (query !== state.lastSearchQuery) return;
        
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
        if (error.name === 'AbortError') {
            console.log('Search cancelled');
            return;
        }
        console.error('Error:', error);
        elements.searchResults.innerHTML = `<div class="empty-state" style="padding: 20px;"><p style="font-size: 13px; color: #ef4444;">Error: ${error.message}</p></div>`;
        showToast(`Search failed: ${error.message}`, 'error');
    } finally {
        state.currentSearchController = null;
    }
}

// New Chat
function newChat() {
    if (confirm('Start a new chat? This will create a new conversation.')) {
        createNewConversation();
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
        const response = await fetch(`${window.location.origin}/api/health`);
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
