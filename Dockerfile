# 1. Base image
FROM python:3.11-slim

# 2. NEW ODBC DRIVER INSTALLATION (Add these lines)
RUN apt-get update && apt-get install -y \
    gnupg2 \
    curl \
    unixodbc \
    unixodbc-dev

RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list

RUN apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql17

# 3. YOUR EXISTING DOCKERFILE CONTINUES HERE
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:create_app()"]