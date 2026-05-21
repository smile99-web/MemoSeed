FROM node:22-alpine

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend ./

EXPOSE 3000

CMD ["npm", "run", "dev"]
