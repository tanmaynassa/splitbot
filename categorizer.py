"""
Auto-categorize Zepto grocery items into expense categories.
Uses keyword matching — covers ~90% of common grocery items.
Unmatched items go to 'Other' (can be fixed manually in the sheet).
"""

# Category rules: keyword → category
# Checked in order, first match wins
CATEGORY_RULES = [
    # Packaged / Junk Food (check early — brand names like Maggi, Kurkure)
    (["maggi", "chips", "kurkure", "karare", "too yumm", "biscuit", "cookie", "namkeen",
      "bhujia", "mixture", "wafer", "nachos", "popcorn", "instant",
      "cup noodle", "top ramen", "yippee", "knorr", "ready to eat",
      "frozen", "pizza", "burger", "fries", "nugget", "momos",
      "samosa", "spring roll", "chocolate", "candy", "toffee",
      "lollipop", "gummy", "donut", "cake", "pastry", "muffin",
      "brownie", "ice cream", "kulfi", "popsicle"], "Packaged/Junk"),

    # Beverages (check early — brand names like Tropicana, Frooti)
    (["cola", "pepsi", "coke", "sprite", "fanta", "thums up", "limca",
      "7up", "mountain dew", "soda", "tonic", "juice", "appy", "frooti",
      "maaza", "real juice", "tropicana", "tea", "chai", "coffee",
      "nescafe", "bru", "green tea", "herbal tea", "kombucha",
      "energy drink", "red bull", "monster", "sting", "beer", "wine",
      "water", "mineral water", "bisleri", "kinley", "electral",
      "ors", "glucon-d", "tang", "rasna", "rooh afza",
      "milkshake", "smoothie", "protein shake"], "Beverages"),

    # Personal Care
    (["shampoo", "conditioner", "soap", "body wash", "face wash",
      "moisturizer", "lotion", "sunscreen", "deodorant", "deo",
      "perfume", "razor", "shaving", "toothpaste", "toothbrush",
      "mouthwash", "floss", "comb", "hair oil", "hair gel",
      "hair cream", "face cream", "serum", "lip balm", "kajal",
      "sanitary", "pad", "tampon", "tissue", "wet wipe",
      "cotton", "band aid", "bandage", "dettol", "sanitizer",
      "hand wash", "nail cutter"], "Personal Care"),

    # Household & Cleaning
    (["detergent", "surf", "ariel", "tide", "vim", "dish wash",
      "dishwash", "harpic", "toilet cleaner", "floor cleaner",
      "lizol", "phenyl", "colin", "glass cleaner", "mop", "broom",
      "scrub", "sponge", "dustbin", "garbage bag", "trash bag",
      "foil", "aluminium foil", "cling wrap", "plastic wrap",
      "paper towel", "napkin", "air freshener", "odonil",
      "mosquito", "repellent", "good knight", "all out",
      "cockroach", "pest", "rat", "matchbox", "candle",
      "bulb", "battery", "tape", "glue"], "Household"),

    # Staples (grains, oil, spices, condiments — check before Fruits/Vegetables)
    (["rice", "atta", "flour", "maida", "suji", "rava", "semolina",
      "dal", "lentil", "chana", "rajma", "moong", "masoor", "toor",
      "urad", "poha", "oats", "muesli", "cornflakes", "cereal",
      "bread", "pav", "roti", "naan", "wheat", "besan", "gram flour",
      "quinoa", "barley", "millets", "ragi", "jowar", "bajra",
      "vermicelli", "sevai", "pasta", "noodle", "spaghetti", "macaroni",
      "salt", "sugar", "pepper", "turmeric", "haldi", "chilli powder",
      "mirch", "cumin", "jeera", "coriander powder", "garam masala",
      "biryani masala", "kitchen king", "sambhar", "rasam", "pickle",
      "achar", "sauce", "ketchup", "mayonnaise", "vinegar",
      "soy sauce", "mustard", "honey", "jam", "jaggery", "gur",
      "mishri", "ajwain", "hing", "asafoetida", "cardamom", "elaichi",
      "clove", "laung", "cinnamon", "dalchini", "bay leaf", "tej patta",
      "star anise", "fennel", "saunf", "poppy seed", "til", "sesame",
      "masala", "paste",
      "oil", "refined", "mustard oil", "sunflower", "groundnut oil",
      "olive oil", "coconut oil", "sesame oil", "rice bran",
      "cooking oil", "vanaspati"], "Staples"),

    # Fruits
    (["banana", "apple", "mango", "orange", "grapes", "grape", "papaya",
      "pomegranate", "watermelon", "melon", "kiwi", "pineapple", "guava",
      "litchi", "lychee", "pear", "peach", "plum", "cherry", "berries",
      "berry", "strawberry", "blueberry", "fig", "custard apple", "sitaphal",
      "chikoo", "sapota", "dragonfruit", "mosambi", "sweet lime", "lemon",
      "lime", "amla", "coconut"], "Fruits"),

    # Vegetables
    (["onion", "potato", "tomato", "capsicum", "carrot", "beans", "cabbage",
      "cauliflower", "broccoli", "spinach", "palak", "methi", "bhindi",
      "okra", "lady finger", "lauki", "gourd", "tori", "ridge gourd",
      "bitter gourd", "karela", "parwal", "peas", "mushroom", "corn",
      "ginger", "garlic", "green chilli", "coriander", "dhaniya", "mint",
      "pudina", "curry leaves", "cucumber", "kheera", "beetroot", "radish",
      "mooli", "pumpkin", "kaddu", "sweet potato", "shakarkandi", "brinjal",
      "baingan", "lettuce", "zucchini", "avocado", "spring onion",
      "baby corn", "shimla mirch", "sabzi", "vegetable", "veggies"], "Vegetables"),

    # Dairy
    (["milk", "dahi", "curd", "yogurt", "paneer", "cheese", "butter",
      "ghee", "cream", "lassi", "chaach", "buttermilk", "khoa", "mawa",
      "amul", "mother dairy", "verka", "milky mist", "epigamia",
      "hung curd", "shrikhand"], "Dairy"),

    # Protein / Meat / Eggs
    (["egg", "chicken", "mutton", "fish", "prawn", "shrimp", "lamb",
      "pork", "meat", "keema", "sausage", "salami", "bacon", "turkey",
      "protein powder", "whey", "protein bar", "tofu", "soya chunks",
      "nutrela"], "Protein"),

    # Healthy Snacks (dry fruits & nuts)
    (["almond", "badam", "cashew", "kaju", "walnut", "akhrot",
      "pistachio", "pista", "raisin", "kishmish", "dates", "khajoor",
      "fig", "anjeer", "dried", "trail mix", "mixed nuts",
      "peanut", "mungfali", "seeds", "chia", "flax", "sunflower seed",
      "pumpkin seed"], "Healthy Snacks"),
]


def categorize_item(item_name: str) -> str:
    """Categorize a grocery item by matching keywords in its name."""
    name_lower = item_name.lower()

    for keywords, category in CATEGORY_RULES:
        for keyword in keywords:
            if keyword in name_lower:
                return category

    return "Other"
