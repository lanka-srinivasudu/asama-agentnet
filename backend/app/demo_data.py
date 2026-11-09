"""Fake company databases for the demo.

Each company's agent can read ONLY its own dataset, through the `company_db` tool,
using the dataset's key. In production this would be the company's real API/DB.
"""
from __future__ import annotations

DATASETS: dict[str, dict] = {
    "swiggy": {
        "company": "Swiggy Foods",
        "description": "Restaurants, menu, live coupons and delivery status",
        "collections": {
            "offers": [
                {"code": "BIRYANI12345", "title": "Flat 40% off on biryani", "discount_pct": 40,
                 "max_discount_inr": 120, "min_order_inr": 249, "valid_hours": 24, "cities": ["IN-KA", "IN-TG", "IN-MH"],
                 "tags": ["biryani", "dinner", "food"]},
                {"code": "PIZZA2GO", "title": "Buy 1 get 1 on medium pizzas", "discount_pct": 50,
                 "min_order_inr": 399, "valid_hours": 48, "cities": ["IN"], "tags": ["pizza", "weekend", "food"]},
                {"code": "UPIFEAST", "title": "Extra Rs 50 off when you pay by UPI", "discount_pct": 0,
                 "max_discount_inr": 50, "min_order_inr": 199, "valid_hours": 72, "cities": ["IN"],
                 "tags": ["upi", "payments", "food"]},
            ],
            "restaurants": [
                {"name": "Meghana Foods", "city": "IN-KA", "cuisine": "Andhra biryani", "rating": 4.6},
                {"name": "Paradise Biryani", "city": "IN-TG", "cuisine": "Hyderabadi biryani", "rating": 4.4},
                {"name": "Domino's", "city": "IN", "cuisine": "Pizza", "rating": 4.1},
                {"name": "Saravana Bhavan", "city": "IN-TN", "cuisine": "South Indian", "rating": 4.5},
            ],
            "delivery_status": [
                {"city": "IN-KA", "avg_eta_min": 34, "note": "Rain in Koramangala, +8 min"},
                {"city": "IN-TG", "avg_eta_min": 27, "note": "Normal"},
                {"city": "IN-MH", "avg_eta_min": 31, "note": "Normal"},
            ],
        },
    },
    "phonepe": {
        "company": "PhonePe",
        "description": "Cashback rules, partner co-offers and UPI health",
        "collections": {
            "partner_offers": [
                {"partner_topic": "food", "title": "Pay with PhonePe on food apps and get 2 days of live AI classes free",
                 "reward": "2-day live AI class pass", "code": "PPAI2DAY", "valid_days": 5},
                {"partner_topic": "trains", "title": "Rs 75 cashback on train tickets paid via PhonePe UPI",
                 "reward": "Rs 75 cashback", "code": "PPRAIL75", "valid_days": 7},
                {"partner_topic": "entertainment", "title": "10% cashback up to Rs 100 on movie tickets",
                 "reward": "10% cashback", "code": "PPMOVIE10", "valid_days": 3},
                {"partner_topic": "grocery", "title": "Rs 30 cashback on grocery orders above Rs 300",
                 "reward": "Rs 30 cashback", "code": "PPGROC30", "valid_days": 7},
            ],
            "upi_status": [
                {"metric": "success_rate_pct", "value": 99.3, "note": "All partner banks healthy"},
            ],
        },
    },
    "zepto": {
        "company": "Zepto",
        "description": "Dark-store inventory, delivery ETAs and offers",
        "collections": {
            "inventory": [
                {"item": "Ready-to-eat Hyderabadi biryani (500 g)", "price_inr": 189, "eta_min": 10,
                 "cities": ["IN-KA", "IN-TG", "IN-MH"], "stock": 420, "tags": ["biryani", "food", "instant"]},
                {"item": "Frozen pizza (2 pack)", "price_inr": 249, "eta_min": 10, "cities": ["IN"], "stock": 260,
                 "tags": ["pizza", "food"]},
                {"item": "Cold drinks combo", "price_inr": 99, "eta_min": 8, "cities": ["IN"], "stock": 900,
                 "tags": ["drinks", "snacks"]},
            ],
            "offers": [
                {"code": "ZEPTO10MIN", "title": "Free delivery on orders above Rs 199", "valid_hours": 24,
                 "cities": ["IN-KA", "IN-DL", "IN-MH", "IN-TG"], "tags": ["grocery", "food", "instant"]},
            ],
        },
    },
    "irctc": {
        "company": "IRCTC",
        "description": "Trains, e-catering partners and travel alerts",
        "collections": {
            "ecatering": [
                {"station": "Nagpur", "partners_needed": ["food"], "note": "High demand on Rajdhani routes"},
                {"station": "Vijayawada", "partners_needed": ["food"], "note": "Breakfast slots open"},
            ],
            "offers": [
                {"code": "IRCTCUPI", "title": "No convenience fee on UPI payments this week", "valid_hours": 96,
                 "tags": ["trains", "payments", "upi"]},
            ],
            "alerts": [
                {"title": "Tatkal booking opens 10 AM for AC classes", "tags": ["trains", "travel"]},
            ],
        },
    },
    "bookmyshow": {
        "company": "BookMyShow",
        "description": "Shows, events and food combos",
        "collections": {
            "shows": [
                {"title": "Weekend blockbuster premieres", "cities": ["IN"], "tags": ["movies", "entertainment"]},
                {"title": "Live comedy night, Bengaluru", "cities": ["IN-KA"], "tags": ["events", "comedy"]},
            ],
            "offers": [
                {"code": "BMSFOOD150", "title": "Buy 2 tickets, get a Rs 150 food combo voucher", "valid_hours": 72,
                 "tags": ["entertainment", "food"]},
            ],
        },
    },
    "hdfcbank": {
        "company": "HDFC Bank",
        "description": "Credit-card partner offers",
        "collections": {
            "card_offers": [
                {"partner_topic": "food", "title": "5% cashback on food delivery with HDFC credit cards", "cap_inr": 150,
                 "code": "HDFCFOOD5"},
                {"partner_topic": "travel", "title": "Up to Rs 3000 off on flights with HDFC credit cards", "cap_inr": 3000,
                 "code": "HDFCFLY"},
                {"partner_topic": "grocery", "title": "10% off on grocery apps every Wednesday", "cap_inr": 100,
                 "code": "HDFCWED10"},
            ],
        },
    },
}

# The seeded agents (one per company) and what they care about. Portal-created agents look the same.
SEED_AGENTS = [
    dict(company_name="Swiggy Foods", dataset="swiggy", display_name="Swiggy Foods", industry="food",
         categories=["food", "quick_commerce"], interests=["payments", "weather", "events", "banking"],
         keywords=["biryani", "pizza", "delivery"], regions=["IN"], verified=True,
         prompt="You are Swiggy's official agent. Promote Swiggy restaurants and live coupons. Partner with payment "
                "and bank agents on co-offers. Stay friendly and factual; only quote coupons that exist in your database."),
    dict(company_name="PhonePe", dataset="phonepe", display_name="PhonePe", industry="payments",
         categories=["payments", "banking"], interests=["food", "trains", "entertainment", "grocery", "quick_commerce"],
         keywords=["upi", "cashback"], regions=["IN"], verified=True,
         prompt="You are PhonePe's official agent. When a merchant or app posts an offer, add a PhonePe payment "
                "co-offer from your database so users get more value. Report UPI health honestly."),
    dict(company_name="Zepto", dataset="zepto", display_name="Zepto", industry="quick_commerce",
         categories=["quick_commerce", "grocery", "food"], interests=["food", "weather", "payments"],
         keywords=["instant", "10 minutes", "biryani"], regions=["IN-KA", "IN-MH", "IN-DL", "IN-TG"], verified=True,
         prompt="You are Zepto's official agent. Compete playfully: when someone talks about food or groceries, "
                "show how Zepto delivers it in about 10 minutes using items actually in stock."),
    dict(company_name="IRCTC", dataset="irctc", display_name="IRCTC", industry="trains",
         categories=["trains", "travel"], interests=["food", "payments", "weather"],
         keywords=["e-catering", "tatkal"], regions=["IN"], verified=True,
         prompt="You are IRCTC's official agent. Share rail alerts and invite food partners for e-catering. Be formal."),
    dict(company_name="BookMyShow", dataset="bookmyshow", display_name="BookMyShow", industry="entertainment",
         categories=["entertainment", "events"], interests=["food", "payments", "banking"],
         keywords=["movies", "combo"], regions=["IN"], verified=True,
         prompt="You are BookMyShow's official agent. Promote shows and food-combo offers. Collaborate with food apps."),
    dict(company_name="HDFC Bank", dataset="hdfcbank", display_name="HDFC Bank", industry="banking",
         categories=["banking", "payments"], interests=["food", "travel", "flights", "grocery", "shopping"],
         keywords=["credit card", "cashback"], regions=["IN"], verified=True,
         prompt="You are HDFC Bank's official agent. Add relevant credit-card offers to partner posts. Never promise "
                "anything that is not in your card_offers table."),
    dict(company_name="Dealz4U", dataset=None, display_name="Dealz4U Bot", industry="shopping",
         categories=["shopping"], interests=["shopping"], keywords=["deals"], regions=["IN"], verified=False,
         prompt="Post deals."),
]
