import pytest
import asyncio
from pymongo.errors import DuplicateKeyError
from return_platform.configuration.application.activation import ActivationService, ActivationConflictError
from return_platform.configuration.domain.release import ReleaseStatus

class MockMongoCollection:
    def __init__(self):
        self.docs = []
        
    async def create_indexes(self, indexes):
        pass
        
    async def insert_one(self, doc):
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = d.get("release_id", str(len(self.docs) + 1))
        self.docs.append(d)
        
    async def find_one(self, filter_doc, session=None):
        await asyncio.sleep(0.001)
        for d in self.docs:
            match = True
            for k, v in filter_doc.items():
                if d.get(k) != v:
                    match = False
                    break
            if match:
                return dict(d)
        return None
        
    async def update_one(self, filter_doc, update_doc, session=None):
        await asyncio.sleep(0.001)
        if update_doc.get("$set", {}).get("status") == ReleaseStatus.ACTIVE:
            if any(d.get("status") == ReleaseStatus.ACTIVE for d in self.docs):
                raise DuplicateKeyError("Duplicate active release")

        for d in self.docs:
            if all(d.get(k) == v for k, v in filter_doc.items()):
                if "$set" in update_doc:
                    d.update(update_doc["$set"])
                class Result:
                    modified_count = 1
                return Result()
                
        class Result:
            modified_count = 0
        return Result()

    async def find_one_and_update(self, filter_doc, update_doc, upsert=False, return_document=None, session=None):
        await asyncio.sleep(0.001)
        target = None
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter_doc.items()):
                target = d
                break
                
        if not target:
            if upsert and filter_doc.get("version", 0) == 0:
                new_doc = {"_id": filter_doc.get("_id")}
                if "$set" in update_doc:
                    new_doc.update(update_doc["$set"])
                self.docs.append(new_doc)
                return dict(new_doc)
            return None
            
        if "$set" in update_doc:
            target.update(update_doc["$set"])
        return dict(target)

    async def count_documents(self, filter_doc):
        count = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter_doc.items()):
                count += 1
        return count
        
    async def delete_many(self, filter_doc):
        self.docs.clear()

class MockSession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    def start_transaction(self):
        return self

class MockMongoClient:
    def __init__(self):
        self.releases = MockMongoCollection()
        self.pointer = MockMongoCollection()
        
    def get_database(self, name):
        return self
        
    def get_collection(self, name):
        if name == "configuration_releases":
            return self.releases
        return self.pointer
        
    def start_session(self):
        return MockSession()

@pytest.mark.asyncio
async def test_concurrent_activation():
    mongodb_client = MockMongoClient()
    service = ActivationService(mongodb_client)
    await service.initialize_indexes()
    
    releases = mongodb_client.releases
    pointer = mongodb_client.pointer
    
    await releases.insert_one({"release_id": "r1", "status": ReleaseStatus.APPROVED, "checksum": "c1"})
    await releases.insert_one({"release_id": "r2", "status": ReleaseStatus.APPROVED, "checksum": "c2"})
    
    # Activate r1
    await service.activate_release("r1")
    
    doc = await pointer.find_one({"_id": "active"})
    assert doc["release_id"] == "r1"
    assert doc["version"] == 1
    
    async def try_activate(rid: str):
        try:
            await service.activate_release(rid)
            return True
        except ActivationConflictError:
            return False
            
    await releases.insert_one({"release_id": "r3", "status": ReleaseStatus.APPROVED, "checksum": "c3"})
    
    results = await asyncio.gather(
        try_activate("r2"),
        try_activate("r3")
    )
    
    # Exactly one should succeed
    success_count = sum(1 for r in results if r)
    assert success_count == 1
    
    actives = await releases.count_documents({"status": ReleaseStatus.ACTIVE})
    assert actives == 1
