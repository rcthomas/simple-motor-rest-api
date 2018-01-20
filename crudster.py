
from datetime import datetime, timedelta
import json
import traceback
from uuid import UUID, uuid4

from bson import ObjectId

from motor import motor_tornado

from tornado import escape, gen, ioloop, web
from traitlets.config.application import Application
from traitlets.config.configurable import Configurable
from traitlets import Int, Float, Unicode, Bool


class _JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return obj.hex
        return json.JSONEncoder.default(self, obj)


class CRUDRequestHandler(web.RequestHandler):
    """Base CRUD API Interface"""

    def initialize(self):
        """Connect to document store"""

        self.db = self.settings["db"]
        self.collection = self.db[self.settings["collection_name"]]
        self.index_args = self.settings.get("index_args", list())

    def write_json(self, document):
        """Format output as JSON"""

        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(escape.utf8(json.dumps(document, cls=_JSONEncoder)))

    def write_dict(self, *args, **kwargs):
        """Format dictionary or parameter list as JSON dictionary"""

        if args:
            if len(args) == 1 and type(args[0]) is dict:
                self.write_json(args[0])
            else:
                raise ValueError
        else:
            self.write_json(kwargs)

    def write_error(self, status_code, **kwargs):
        """Format error as JSON dictionary"""

        if self.settings.get("serve_traceback") and "exc_info" in kwargs:
            self.set_header('Content-Type', 'text/plain')
            for line in traceback.format_exception(*kwargs["exc_info"]):
                self.write(line)
        else:
            self.write_dict(status_code=status_code, reason=self._reason)
        self.finish()

    def decode_and_validate_document(self):
        """Extract and validate documents for insert/update

        Decodes document from JSON request body.  Validation here is applied
        before insertion to MongoDB.  Newer MongoDB versions support schema
        validation but a hook is included to cover anything on top of that.
        """

        document = escape.json_decode(self.request.body)
        self.validate_document(document)
        return document

    def validate_document(self, document):
        """Validate document before insertion

        Raise web.HTTPError(400) if there is a validation error."""

        pass

    @gen.coroutine
    def create_indices(self):
        """Create indices

        This runs during the first POST operation."""

        for (args, kwargs) in self.index_args:
            yield self.collection.create_index(*args, **kwargs)

    @gen.coroutine
    def post(self, uuid):
        """Store new document"""

        # API determines document ID, not client.

        if uuid:
            raise web.HTTPError(400)
        uuid = uuid4()

        # Decode, validate, and insert document.

        document = self.decode_and_validate_document()
        result = yield self.collection.insert_one(dict(document=document, 
            uuid=uuid))

        # Create any indices.

        yield self.create_indices()

        # Return inserted document ID for client future reference.

        self.write_dict(uuid=uuid)

    @gen.coroutine
    def get(self, uuid):
        """Retrieve stored documents"""

        if uuid:
            yield self.get_one_document(uuid)
        else:
            yield self.get_many_documents()

    @gen.coroutine
    def get_one_document(self, uuid):
        """Retrieve one document"""

        result = yield self.collection.find_one({"uuid": UUID(uuid)})
        if result:
            self.write_dict(result["document"])
        else:
            raise web.HTTPError(404)

    @gen.coroutine
    def get_many_documents(self):
        """Retrieve a list of documents"""
        # FIXME: skip, limit, sort, 
        # http://motor.readthedocs.io/en/stable/tutorial-tornado.html#querying-for-more-than-one-document

        cursor = self.collection.find()
        documents = dict()
        while (yield cursor.fetch_next):
            result = cursor.next_object()
            documents[result["uuid"].hex] = result["document"]
        self.write_dict(documents)

    @gen.coroutine
    def put(self, uuid):
        """Replace existing document"""

        # Document ID is required.

        if not uuid:
            raise web.HTTPError(400)

        # Decode, validate, and replace document.

        document = self.decode_and_validate_document()
        result = yield self.collection.find_one_and_update(
                {"uuid": UUID(uuid)}, 
                {"$set": dict(document=document)})

        # Return empty document if update succeed.

        if result:
            self.write_dict()
        else:
            raise web.HTTPError(404)

    @gen.coroutine
    def delete(self, uuid):
        """Delete document"""

        # Find document by ID and remove it.

        result = yield self.collection.delete_one({"uuid": UUID(uuid)})

        # Return empty document if it succeeded.

        if result.deleted_count == 1:
            self.write_dict()
        else:
            raise web.HTTPError(404)


class MongoDB(Configurable):

    database_name = Unicode("crudster",
        help="MongoDB database name"
    ).tag(config=True)

    collection_name = Unicode("data",
        help="MongoDB database name"
    ).tag(config=True)

    uri = Unicode("mongodb://127.0.0.1:27017",
        help="MongoDB server URI"
    ).tag(config=True)

    initialize_database = Bool(False,
        help="Clear any pre-existing database"
    ).tag(config=True)


class Crudster(Application):

    classes = [MongoDB]

    api_prefix = Unicode("/",
        help="API URL prefix"
    ).tag(config=True)

    description = Unicode("Simple CRUD REST API")
    
    name = Unicode("crudster")

    port = Int(8888, 
        help="Request handler port"
    ).tag(config=True)

    version = Unicode("0.0.1")

    def init_mongodb(self):
        self.mongodb = MongoDB(config=self.config)

    def initialize(self, *args, **kwargs):
        super().initialize(*args, **kwargs)
        self.init_mongodb()

        self.client = motor_tornado.MotorClient(self.mongodb.uri)

        if self.mongodb.initialize_database:
            self.client.drop_database(self.mongodb.database_name)

        self.db = self.client[self.mongodb.database_name]

        self.settings = dict(db=self.db,
                collection_name=self.mongodb.collection_name)

    def start(self):
        self.app = web.Application([ 
            (r"{}([0-9a-f]{{12}}4[0-9a-f]{{3}}[89ab][0-9a-f]{{15}})?".format(self.api_prefix), CRUDRequestHandler),
        ], **self.settings)


def start_crudster():
    crudster = Crudster()
    crudster.initialize()
    crudster.start()
    return crudster


def main():
    crudster = start_crudster()
    crudster.app.listen(crudster.port)
    ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
