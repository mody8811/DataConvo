FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gnupg2 \
    curl \
    unixodbc \
    unixodbc-dev \
    libgssapi-krb5-2 \
    openssl

# Microsoft ODBC driver for Debian 12
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list

# Install ODBC Driver 18
RUN apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Set LD_LIBRARY_PATH so the system can find the driver libraries
ENV LD_LIBRARY_PATH=/opt/microsoft/msodbcsql18/lib64:$LD_LIBRARY_PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:create_app()"]