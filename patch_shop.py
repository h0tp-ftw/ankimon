shop1_path = 'src/Ankimon/pyobj/ankimon_shop.py'
with open(shop1_path, 'r') as f:
    shop1_content = f.read()

import re

old_shop1 = """        if shop_data:
            if shop_data.get("items") and shop_data.get(
                "date"
            ) == datetime.now().strftime("%Y-%m-%d"):
                return shop_data.get("items")

        seed = datetime.now().strftime("%Y-%m-%d")"""

new_shop1 = """        from ..utils import get_current_anki_date_str
        current_date_str = get_current_anki_date_str()

        if shop_data:
            if shop_data.get("items") and shop_data.get(
                "date"
            ) == current_date_str:
                return shop_data.get("items")

        seed = current_date_str"""

old_shop2 = """        if shop_data:
            if shop_data.get("technical_machines") and shop_data.get(
                "date"
            ) == datetime.now().strftime("%Y-%m-%d"):
                return shop_data.get("technical_machines")

        tm_pool = self.get_tm_pool()
        seed = datetime.now().strftime("%Y-%m-%d")"""

new_shop2 = """        from ..utils import get_current_anki_date_str
        current_date_str = get_current_anki_date_str()

        if shop_data:
            if shop_data.get("technical_machines") and shop_data.get(
                "date"
            ) == current_date_str:
                return shop_data.get("technical_machines")

        tm_pool = self.get_tm_pool()
        seed = current_date_str"""

shop1_content = shop1_content.replace(old_shop1, new_shop1)
shop1_content = shop1_content.replace(old_shop2, new_shop2)

with open(shop1_path, 'w') as f:
    f.write(shop1_content)
