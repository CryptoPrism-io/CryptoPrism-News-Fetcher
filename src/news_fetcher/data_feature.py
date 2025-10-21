def extract_headlines(news_data: dict) -> list:
    """
    Extract headlines from news data.

    :param news_data: Parsed JSON response containing articles
    :return: List of headline strings
    """
    articles = news_data.get("articles", [])
    return [article.get("title", "") for article in articles]
