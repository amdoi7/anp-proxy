#!/usr/bin/env python3
"""Setup test database with DID service mappings for AI agent infrastructure test."""

import asyncio
import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from anp_proxy.common.config import DatabaseConfig
from anp_proxy.common.db_base import DatabaseAdapter


async def setup_test_database():
    """Setup test database with DID service mappings."""

    print("🗄️  Setting up test database with DID service mappings...")

    # Create database configuration for AWS RDS
    db_config = DatabaseConfig(
        enabled=True,
        host="agentinfra.cgzu4q6aej87.us-east-1.rds.amazonaws.com",
        port=3306,
        user="admin",
        password="NewDb-30487620",
        database="did_db",
        min_connections=2,
        max_connections=20,
    )

    # Initialize database adapter
    db_adapter = DatabaseAdapter(db_config)
    db_adapter.initialize()

    # Two different DIDs for testing
    did_anpproxy1 = "did:wba:didhost.cc:anpproxy1"
    did_anpproxy2 = "did:wba:didhost.cc:anpproxy2"

    # Test DID service mappings for two different DIDs
    test_services = [
        # ANP Proxy 1 services
        {"did": did_anpproxy1, "service_url": "api.agent.com/anpproxy1"},
        # ANP Proxy 2 services
        {"did": did_anpproxy2, "service_url": "api.agent.com/anpproxy2"},
    ]

    # Insert test data
    success_count = 0
    total_count = len(test_services)

    for service_data in test_services:
        did = service_data["did"]
        service_url = service_data["service_url"]

        success = db_adapter.register_did_service(did, service_url)
        if success:
            success_count += 1
            print(f"  ✅ {did} -> {service_url}")
        else:
            print(f"  ❌ Failed: {did} -> {service_url}")

    print(
        f"\n📊 Database setup completed: {success_count}/{total_count} services registered"
    )

    # Verify the data
    print("\n🔍 Verifying database content:")
    for did in [did_anpproxy1, did_anpproxy2]:
        service_urls = db_adapter.get_services_by_did(did)
        print(f"  📋 {did}: {len(service_urls)} services")
        for url in service_urls:
            print(f"     - {url}")

    # Close database connection
    db_adapter.close()
    print("\n✅ Database setup complete! Ready for AI agent infrastructure test.")


if __name__ == "__main__":
    asyncio.run(setup_test_database())
