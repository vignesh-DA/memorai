# Frontend Structure

This folder contains the separated frontend files for the Memory AI Chat interface.

## Files

- **index.html** - Main HTML structure with all input fields and UI elements
- **styles.css** - Complete styling for the interface
- **app.js** - JavaScript functionality with all API connections

## Features

### HTML (`index.html`)
- Sidebar with user controls and statistics
- Main chat container with message display
- Input area with textarea and send button
- Search panel for memory lookup
- Toast notification container

### CSS (`styles.css`)
- Clean, modern design with smooth animations
- Responsive layout (mobile-friendly)
- Custom scrollbar styling
- Loading indicators and transitions
- Toast notification animations

### JavaScript (`app.js`)
- **State Management**: Tracks conversation turns, loading state, user info
- **API Integration**: 
  - `POST /api/v1/conversation` - Send messages and get AI responses
  - `GET /api/v1/memories/{user_id}/stats` - Get memory statistics
  - `POST /api/v1/memories/{user_id}/search` - Search memories
  - `GET /api/v1/health` - Check API health
- **Event Handlers**: Auto-resize textarea, keyboard shortcuts, button clicks
- **UI Updates**: Dynamic message rendering, loading indicators, notifications

## API Endpoints Used

```javascript
const API_BASE = 'http://localhost:8000/api/v1';

// Send conversation message
POST /api/v1/conversation
Body: {
  user_id: string,
  turn_number: number,
  message: string,
  include_memories: boolean
}

// Get memory statistics
GET /api/v1/memories/{user_id}/stats

// Search memories
POST /api/v1/memories/{user_id}/search?query={query}&top_k={limit}

// Health check
GET /api/v1/health
```

## How to Access

1. Make sure the FastAPI server is running:
   ```bash
   uvicorn app.main:app --reload
   ```

2. Open your browser and go to:
   ```
   http://localhost:8000/ui
   ```

## Keyboard Shortcuts

- **Enter**: Send message
- **Shift+Enter**: New line in message
- **Enter** (in search): Execute search

## Customization

- **API URL**: Change `API_BASE` in `app.js` line 2
- **Colors**: Modify values in `styles.css`
- **Layout**: Adjust grid/flex properties in `styles.css`
- **Features**: Add new functions in `app.js`
