from productivity_bot.bootstrap.application import create_app
from productivity_bot.config import get_settings

app = create_app(get_settings())
