// Vercel Web Analytics integration
import { inject } from '@vercel/analytics';

// Initialize analytics on page load
inject({
  mode: 'auto', // auto-detects development vs production
  framework: 'react'
});
