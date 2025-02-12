FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gnupg2 \
    curl \
    unixodbc \
    unixodbc-dev \
    libgssapi-krb5-2 \
    openssl

# Add Microsoft repository and keys properly
RUN curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list

# Install both ODBC Driver 18 and ODBC Driver 17
RUN apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 msodbcsql17

# Set LD_LIBRARY_PATH so the system can find both driver's libraries
ENV LD_LIBRARY_PATH=/opt/microsoft/msodbcsql18/lib64:/opt/microsoft/msodbcsql17/lib64:$LD_LIBRARY_PATH

WORKDIR /app

# Copy and install requirements first (for better caching)
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install gunicorn  # Explicitly install gunicorn

# Copy the rest of the application
COPY . .

# Set default port
ENV PORT=10000

# Expose the port
EXPOSE ${PORT}

# Ensure we’re using the full path to gunicorn when starting the app
CMD ["/usr/local/bin/gunicorn", "--bind", "0.0.0.0:10000", "app:create_app()"]