import pandas as pd

from emporio.settings import RAW_DATA_DIR
from emporio.text_utils import normalize


def read_table(table, columns=None):
    file_path = RAW_DATA_DIR / f'desafio_tecnico_ai_eng - {table}.csv'
    return pd.read_csv(file_path, usecols=columns)


def load_products():
    products = read_table('products')
    categories = read_table('categories', columns=['category_id', 'name'])
    categories = categories.rename(columns={'name': 'category'})
    promotions = read_table('promotions')

    # only active promos; if a product has more than one, keep the biggest discount
    active = promotions[promotions['is_active'] == 1]
    active = active.sort_values('discount_percent')
    active = active.drop_duplicates('product_id', keep='last')
    active = active.rename(columns={'description': 'promotion', 'discount_percent': 'discount'})
    active = active[['product_id', 'promotion', 'discount']]

    products = products.merge(categories, on='category_id', how='left')
    products = products.merge(active, on='product_id', how='left')
    products['discount'] = products['discount'].fillna(0)

    discount_factor = 1 - products['discount'] / 100
    products['final_price'] = (products['price_brl'] * discount_factor).round(2)

    # one column with everything the search looks at, already without accents
    search_text = products['name'] + ' ' + products['category'] + ' ' + products['description']
    products['search'] = search_text.map(normalize)
    return products


PRODUCTS = load_products()
ORDERS = read_table('orders')
ORDER_ITEMS = read_table('order_items')
# LGPD: only what is needed to check who owns an order, phone and e-mail are never loaded
CUSTOMERS = read_table('customers', columns=['customer_id', 'name'])
