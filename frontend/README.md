# AuroraCoder Frontend

A beautiful, modern chat interface for AuroraCoder - your intelligent coding companion.

## Features

- 🎨 **Dark theme** with elegant gradient accents
- 💬 **Real-time streaming** responses via Server-Sent Events
- 🧠 **Thinking visualization** - See the AI's reasoning process
- 🛠️ **Tool call display** - Track tool usage with collapsible details
- 📱 **Responsive design** - Works on desktop and mobile
- ⚡ **Fast** - Built with Vite + React

## Quick Start

### Prerequisites

- Node.js 18+ 
- npm or yarn
- Backend API running on port 8080

### Installation

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The frontend will be available at `http://localhost:3000`

### Production Build

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

## Project Structure

```
frontend/
├── public/
│   └── favicon.svg
├── src/
│   ├── components/
│   │   ├── ChatMessage.jsx    # Individual message component
│   │   ├── ThinkingIndicator.jsx
│   │   └── WelcomeScreen.jsx  # Initial welcome with examples
│   ├── services/
│   │   └── api.js             # API client with SSE streaming
│   ├── styles/
│   │   └── index.css          # Complete styling
│   ├── App.jsx                # Main application
│   └── main.jsx               # Entry point
├── index.html
├── package.json
└── vite.config.js
```

## API Integration

The frontend connects to the backend API via:
- `POST /api/chat` - Start a new chat (SSE streaming)
- `POST /api/chat/continue` - Continue after max iterations
- `GET /api/conversations` - List conversations
- `GET /api/conversations/:id` - Get conversation details
- `DELETE /api/conversations/:id` - Delete conversation

See `docs/API_REQUIREMENTS.md` for complete API documentation.

## Customization

### Colors & Theme

Edit CSS variables in `src/styles/index.css`:

```css
:root {
  --accent-primary: #8b5cf6;    /* Purple */
  --accent-secondary: #06b6d4;  /* Cyan */
  --bg-primary: #0a0a0f;        /* Dark background */
  /* ... more variables */
}
```

### Fonts

The interface uses:
- **Outfit** - Sans-serif for UI text
- **JetBrains Mono** - Monospace for code

## License

MIT License - See project root for details.
