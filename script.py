import asyncio  
from cognee.infrastructure.databases.relational import get_relational_engine  
async def init(): await get_relational_engine().create_database()  
asyncio.run(init())  
