"""Drop the memories table."""
import asyncio
from app.database import db_manager
from sqlalchemy import text


async def drop_table():
    await db_manager.initialize()
    async with db_manager.get_session() as sess:
        await sess.execute(text('DROP TABLE IF EXISTS memories CASCADE;'))
        await sess.commit()
    print('✅ Table dropped successfully')
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(drop_table())
