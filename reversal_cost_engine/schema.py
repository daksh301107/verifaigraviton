"""
Demo schema for the dependency-graph reversal-cost approach.

Small, realistic set of tables with real foreign keys — enough to prove
the concept without needing a production-scale database.

    users ─┬─> orders ──> payments
           ├─> subscriptions ──> invoices
           └─> (reviews reference products, not users)

    products ──> reviews
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, ForeignKey, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    deleted = Column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="active")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    amount_inr = Column(Float)
    charged = Column(Boolean, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="active")


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"))
    sent = Column(Boolean, default=True)


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    rating = Column(Integer)


def build_demo_db(path="demo.db"):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # ---- seed data: one product with a realistic multi-hop dependent chain ----
    u1 = User(id=1, name="Daksh")
    p1 = Product(id=1, name="Wireless Mouse")
    session.add_all([u1, p1])
    session.commit()

    o1 = Order(id=1, user_id=1, status="active")
    o2 = Order(id=2, user_id=1, status="pending")
    session.add_all([o1, o2])
    session.commit()

    pay1 = Payment(id=1, order_id=1, amount_inr=799, charged=True)
    pay2 = Payment(id=2, order_id=2, amount_inr=499, charged=True)
    session.add_all([pay1, pay2])
    session.commit()

    r1 = Review(id=1, product_id=1, rating=4)
    r2 = Review(id=2, product_id=1, rating=5)
    r3 = Review(id=3, product_id=1, rating=3)
    session.add_all([r1, r2, r3])
    session.commit()

    sub1 = Subscription(id=1, user_id=1, status="active")
    session.add(sub1)
    session.commit()

    inv1 = Invoice(id=1, subscription_id=1, sent=True)
    session.add(inv1)
    session.commit()

    session.close()
    return engine
