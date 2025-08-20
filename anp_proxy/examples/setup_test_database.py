#!/usr/bin/env python3
"""Setup test database with DID service mappings for AI agent infrastructure test."""

import asyncio
import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from anp_proxy.common.db_base import execute_query, execute_upsert


async def setup_test_database():
    """Setup test database with DID service mappings."""

    print("🗄️  Setting up test database with DID service mappings...")

    # 确保表存在（KISS 版本直接执行 DDL）
    ddl = """
    CREATE TABLE IF NOT EXISTS did_proxy_path (
        id INT AUTO_INCREMENT PRIMARY KEY,
        did VARCHAR(255) NOT NULL,
        proxy_path VARCHAR(512) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_did (did),
        UNIQUE KEY unique_did_service (did, proxy_path)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    # 使用 execute_query 执行 DDL（fetch 不会使用）
    try:
        execute_query(ddl)
    except Exception:
        pass

    # Two different DIDs for testing
    did_anpproxy1 = "did:wba:didhost.cc:anpproxy1"
    did_anpproxy2 = "did:wba:didhost.cc:anpproxy2"

    # Test DID service mappings for two different DIDs
    test_services = [
        # ANP Proxy 1 services
        {"did": "did:wba:didhost.cc:test:public", "proxy_path": "agents/"},
    ]

    # Insert test data
    success_count = 0
    total_count = len(test_services)

    for service_data in test_services:
        did = service_data["did"]
        proxy_path = service_data["proxy_path"]

        sql = (
            "INSERT INTO did_proxy_path (did, proxy_path, created_at, updated_at) "
            "VALUES (%s, %s, NOW(), NOW()) ON DUPLICATE KEY UPDATE updated_at = NOW()"
        )
        success = execute_upsert(sql, (did, proxy_path)) > 0
        if success:
            success_count += 1
            print(f"  ✅ {did} -> {proxy_path}")
        else:
            print(f"  ❌ Failed: {did} -> {proxy_path}")

    print(
        f"\n📊 Database setup completed: {success_count}/{total_count} services registered"
    )

    # Verify the data
    print("\n🔍 Verifying database content:")
    for did in [did_anpproxy1, did_anpproxy2]:
        rows = execute_query(
            "SELECT proxy_path FROM did_proxy_path WHERE did = %s ORDER BY created_at",
            (did,),
        )
        proxy_paths = [row["proxy_path"] for row in rows]
        print(f"  📋 {did}: {len(proxy_paths)} services")
        for path in proxy_paths:
            print(f"     - {path}")

    print("\n✅ Database setup complete! Ready for AI agent infrastructure test.")


if __name__ == "__main__":
    asyncio.run(setup_test_database())
