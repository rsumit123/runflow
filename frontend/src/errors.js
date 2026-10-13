// The message to show for a failed API call. FastAPI's `detail` is a string for
// our own errors but a list of {loc, msg, type} for 422 validation errors;
// rendering that list as a React child would throw, so always return a string.
export function errorText(err, fallback) {
  const detail = err && err.response && err.response.data && err.response.data.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d.msg === 'string' ? d.msg : (typeof d === 'string' ? d : '')))
      .filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  return fallback;
}
