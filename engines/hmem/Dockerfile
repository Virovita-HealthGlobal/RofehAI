FROM node:22-slim
WORKDIR /app
RUN npm install -g tsx@4
COPY reference ./reference
COPY service ./service
ENV HMEM_DIR=/data HMEM_PORT=8910
EXPOSE 8910
CMD ["tsx", "service/server.ts"]
