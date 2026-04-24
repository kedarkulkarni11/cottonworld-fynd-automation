FROM node:18-alpine

WORKDIR /app

# Install dependencies first (better layer caching)
COPY package.json package-lock.json* ./
RUN npm install --omit=dev

# Copy app sources
COPY . .

EXPOSE 8080

CMD ["npm", "start"]
