FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt greenlet

COPY . .

EXPOSE 8000

# --proxy-headers / --forwarded-allow-ips: nginx terminates TLS, so without
# these the MCP endpoint's /mcp -> /mcp/ redirect is emitted as http:// and MCP
# clients refuse to follow it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
