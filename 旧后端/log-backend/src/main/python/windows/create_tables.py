"""Create the database and one table for each of the five log categories."""
from .schema import create_database_and_tables

if __name__ == "__main__":
    create_database_and_tables()
    print("数据库及五类日志表创建完成。")
