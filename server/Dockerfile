FROM python:3.12-slim
WORKDIR /app
COPY stateless_mcp_server.py .
ENV PORT=8080
EXPOSE 8080
USER 65532:65532
ENTRYPOINT ["python3", "stateless_mcp_server.py"]
