// Runs before `react-scripts build`. Without REACT_APP_API_URL the bundle would
// silently point at http://localhost:8000/api, so refuse to build instead.
if (!process.env.REACT_APP_API_URL || !process.env.REACT_APP_API_URL.trim()) {
  console.error(
    '\nBuild stopped: REACT_APP_API_URL is not set.\n' +
    'A production build needs the API address, for example:\n' +
    '  REACT_APP_API_URL=https://runflow-api.skdev.one/api npm run build\n' +
    'On Vercel, set it under Project Settings > Environment Variables.\n'
  );
  process.exit(1);
}
