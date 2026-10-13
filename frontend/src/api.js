import axios from 'axios';
import { publishGarminWarning } from './garminWarning';

export const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

const warningIn = (response) => {
  const data = response && response.data;
  return data && typeof data === 'object' && typeof data.garmin_warning === 'string'
    ? data.garmin_warning
    : null;
};

// Any response can carry a `garmin_warning`; show it app-wide instead of
// making every call site remember to.
// A 401 anywhere but the auth calls means the session has gone: send the user
// to log in, and bring them back here afterwards.
api.interceptors.response.use(
  (response) => {
    const warning = warningIn(response);
    if (warning) publishGarminWarning(warning);
    return response;
  },
  (error) => {
    const warning = warningIn(error.response);
    if (warning) publishGarminWarning(warning);
    const status = error.response && error.response.status;
    const url = (error.config && error.config.url) || '';
    const onAuthPage = ['/login', '/signup'].includes(window.location.pathname);
    if (status === 401 && !url.startsWith('/auth/') && !onAuthPage) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.assign(`/login?next=${next}`);
    }
    return Promise.reject(error);
  },
);

export default api;
