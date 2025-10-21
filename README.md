# NewsFetcher - CryptoPrism Database Platform

A Python-based news aggregation system for cryptocurrency and financial news, designed to fetch, process, and store news data from multiple sources including CoinDesk and CryptoCompare APIs.

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Documentation](#api-documentation)
- [Database Schema](#database-schema)
- [Development](#development)
- [Testing](#testing)
- [Contributing](#contributing)

## 🚀 Overview

NewsFetcher is part of the CryptoPrism Database Platform, designed to:

- **Aggregate news** from multiple cryptocurrency and financial news sources
- **Process and normalize** article data for consistent storage
- **Store structured data** in PostgreSQL database
- **Provide APIs** for news consumption and analysis
- **Support real-time feeds** for trading signals and market analysis

## ✨ Features

### News Sources
- **CoinDesk Legacy News API (v1)** - Comprehensive news articles with metadata
- **CryptoCompare News API** - Real-time cryptocurrency news
- **Extensible architecture** for additional news sources

### Data Processing
- **Article metadata extraction** (title, summary, URL, images, timestamps)
- **Category and source classification**
- **Duplicate detection and deduplication**
- **Timestamp normalization and tracking**

### Database Integration
- **PostgreSQL storage** with optimized schemas
- **Batch insertion** for high-throughput processing
- **Connection pooling** for reliability
- **Data integrity** with proper constraints

### API Support
- **RESTful endpoints** for news consumption
- **Filtering capabilities** by category, source, and time ranges
- **Pagination support** for large datasets
- **Rate limiting** and error handling

## 📁 Project Structure

```
NewsFetcher/
├── src/
│   └── news_fetcher/
│       ├── __init__.py                 # Package initialization
│       ├── api_connector.py            # Generic API connection utilities
│       ├── cryptocompare_sample.py     # CryptoCompare API implementation
│       ├── data_feature.py             # Data extraction utilities
│       ├── data_organiser.py           # Data processing and organization
│       ├── db_connector.py             # Database connection and operations
│       └── db_test.py                  # Database connectivity testing
├── docs/
│   └── crypto_news_api.md              # API documentation template
├── .env                                # Environment configuration
├── requirements.txt                    # Python dependencies
├── COINDESK_API_KNOWLEDGE_BASE.md      # Complete CoinDesk API reference
└── README.md                           # This file
```

## 🛠 Installation

### Prerequisites
- Python 3.8+
- PostgreSQL 12+
- pip package manager

### Setup Steps

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd NewsFetcher
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file with the following configuration:

```env
# API credentials
API_KEY=<your_coindesk_api_key>
CRYPTOCOMPARE_API_KEY=<your_cryptocompare_api_key>

# Database Configuration
DB_HOST=your_db_host
DB_PORT=5432
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_db_name

# Complete Database URL
DB_URL=postgresql://user:password@host:port/database
```

### API Configuration

#### CoinDesk API
- Base URL: `/data/news/v1/`
- Authentication: Bearer token
- Rate limits: Check API documentation

#### CryptoCompare API
- Base URL: `https://min-api.cryptocompare.com/data/v2/news/`
- Authentication: API key parameter
- Language: EN (English)

## 🔧 Usage

### Basic Usage

#### Test Database Connection
```bash
python -m src.news_fetcher.db_test
```

#### Fetch CryptoCompare News
```bash
python -m src.news_fetcher.cryptocompare_sample
```

### Programmatic Usage

#### Fetching News Data
```python
from src.news_fetcher.api_connector import fetch_news
from src.news_fetcher.data_feature import extract_headlines
from src.news_fetcher.data_organiser import organise_headlines
from src.news_fetcher.db_connector import push_headlines

# Fetch news from API
news_data = fetch_news("v1/article/list", {"limit": 20})

# Extract headlines
headlines = extract_headlines(news_data)

# Organize with timestamps
organized_data = organise_headlines(headlines)

# Store in database
push_headlines(organized_data)
```

#### Working with CoinDesk API
```python
# Fetch latest articles
articles = fetch_news("v1/article/list", {
    "limit": 50,
    "from": "2025-09-30T00:00:00Z"
})

# Filter by category
market_news = fetch_news("v1/article/list", {
    "category": "markets",
    "limit": 20
})

# Get available sources
sources = fetch_news("v1/source/list")

# Get categories
categories = fetch_news("v1/category/list")
```

## 📚 API Documentation

### CoinDesk Legacy News API (v1)

The complete API reference is available in [COINDESK_API_KNOWLEDGE_BASE.md](./COINDESK_API_KNOWLEDGE_BASE.md).

#### Key Endpoints:
- `GET /data/news/v1/article/list` - Fetch articles with filtering
- `GET /data/news/v1/source/list` - Get available news sources
- `GET /data/news/v1/category/list` - Get article categories
- `GET /data/news/v1/feed_category/list` - Get source-category mappings

#### Example Requests:
```bash
# Latest 20 articles
curl "https://api.coindesk.com/data/news/v1/article/list?limit=20"

# Articles since timestamp
curl "https://api.coindesk.com/data/news/v1/article/list?from=2025-09-30T00:00:00Z"

# Filter by category and source
curl "https://api.coindesk.com/data/news/v1/article/list?category=markets&source=coindesk"
```

### CryptoCompare News API

```python
import requests

url = "https://min-api.cryptocompare.com/data/v2/news/"
params = {
    "lang": "EN",
    "api_key": "your_api_key"
}
response = requests.get(url, params=params)
```

## 🗄️ Database Schema

### Tables

#### `news_headlines`
```sql
CREATE TABLE news_headlines (
    id SERIAL PRIMARY KEY,
    headline TEXT NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

#### Future Schema Extensions
```sql
-- Articles table for full article data
CREATE TABLE articles (
    id VARCHAR(255) PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT UNIQUE NOT NULL,
    image_url TEXT,
    published_at TIMESTAMP WITH TIME ZONE,
    source_id VARCHAR(100),
    category_id VARCHAR(100),
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Sources reference table
CREATE TABLE sources (
    id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

-- Categories reference table
CREATE TABLE categories (
    id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT
);
```

## 🧪 Testing

### Unit Tests
```bash
# Run all tests
python -m pytest

# Run specific test modules
python -m pytest tests/test_api_connector.py
python -m pytest tests/test_db_connector.py
```

### Integration Tests
```bash
# Test database connectivity
python -m src.news_fetcher.db_test

# Test CryptoCompare API
python -m src.news_fetcher.cryptocompare_sample
```

### Manual Testing
```bash
# Test API endpoints
curl -X GET "https://api.coindesk.com/data/news/v1/source/list"

# Test database connection
psql -h your_host -U your_user -d your_database -c "SELECT 1;"
```

## 📊 Development

### Development Environment Setup

1. **Install development dependencies**
   ```bash
   pip install -r requirements-dev.txt
   ```

2. **Set up pre-commit hooks**
   ```bash
   pre-commit install
   ```

3. **Run code formatting**
   ```bash
   black src/
   isort src/
   ```

4. **Run linting**
   ```bash
   flake8 src/
   pylint src/
   ```

### Adding New News Sources

1. **Create new API module** in `src/news_fetcher/`
2. **Implement fetch function** following the pattern in existing modules
3. **Add data extraction logic** for the specific API response format
4. **Update `__init__.py`** to include the new module
5. **Add configuration** to `.env` for API credentials

### Code Style Guidelines

- **PEP 8** compliance for Python code
- **Type hints** for function parameters and return values
- **Docstrings** for all functions and classes
- **Error handling** with appropriate exception catching
- **Logging** for debugging and monitoring

## 🤝 Contributing

### Contribution Guidelines

1. **Fork the repository**
2. **Create feature branch** (`git checkout -b feature/amazing-feature`)
3. **Write tests** for new functionality
4. **Ensure code quality** (linting, formatting, tests pass)
5. **Commit changes** (`git commit -m 'Add amazing feature'`)
6. **Push to branch** (`git push origin feature/amazing-feature`)
7. **Open Pull Request**

### Development Priorities

1. **Enhanced error handling** and retry mechanisms
2. **Comprehensive test coverage** (unit and integration)
3. **Performance optimization** for high-throughput scenarios
4. **Additional news sources** integration
5. **Real-time streaming** capabilities
6. **Advanced filtering** and search functionality
7. **Monitoring and alerting** system
8. **Docker containerization** for deployment

## 📋 Roadmap

### Phase 1: Core Functionality ✅
- [x] Basic API connectivity
- [x] Database integration
- [x] Data processing pipeline
- [x] Configuration management

### Phase 2: Enhanced Features 🚧
- [ ] Complete CoinDesk API integration
- [ ] Advanced error handling
- [ ] Comprehensive testing
- [ ] Performance optimization

### Phase 3: Production Ready 📋
- [ ] Docker containerization
- [ ] CI/CD pipeline
- [ ] Monitoring and logging
- [ ] Documentation completion
- [ ] Security hardening

### Phase 4: Advanced Features 📋
- [ ] Real-time data streaming
- [ ] Machine learning integration
- [ ] Advanced analytics
- [ ] Multi-language support
- [ ] GraphQL API

## 📄 License

This project is part of the CryptoPrism Database Platform. Please refer to the main project license for usage terms.

## 🆘 Support

For issues, questions, or contributions:

1. **Check existing issues** in the project repository
2. **Create new issue** with detailed description
3. **Join discussions** in project forums
4. **Review documentation** in `/docs` directory

---

**Built with ❤️ for the CryptoPrism Database Platform**

*Last updated: September 30, 2025*