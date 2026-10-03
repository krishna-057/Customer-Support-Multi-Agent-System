"""Insert versioned synthetic support articles after migrations."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support_system.db import database_url
from support_system.support_knowledge import seed_articles


def main() -> None:
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            inserted, updated = seed_articles(session)
        print(f"Support articles: {inserted} inserted, {updated} updated")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
