#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiomysql
import pytest

from connectors.byoc import Filter
from connectors.filtering.validation import SyncRuleValidationResult
from connectors.source import DataSourceConfiguration
from connectors.sources.mysql import (
    MySQLAdvancedRulesValidator,
    MySqlDataSource,
    NoDatabaseConfiguredError,
)
from connectors.sources.tests.support import create_source
from connectors.tests.commons import AsyncIterator


def immutable_doc(**kwargs):
    return frozenset(kwargs.items())


ADVANCED_SNIPPET = "advanced_snippet"

DATABASE = "database"

TABLE_ONE = "table1"
TABLE_TWO = "table2"
TABLE_THREE = "table3"

DOC_ONE = immutable_doc(id=1, text="some text 1")
DOC_TWO = immutable_doc(id=2, text="some text 2")
DOC_THREE = immutable_doc(id=3, text="some text 3")
DOC_FOUR = immutable_doc(id=4, text="some text 4")
DOC_FIVE = immutable_doc(id=5, text="some text 5")
DOC_SIX = immutable_doc(id=6, text="some text 6")
DOC_SEVEN = immutable_doc(id=7, text="some text 7")
DOC_EIGHT = immutable_doc(id=8, text="some text 8")

TABLE_ONE_QUERY_ALL = "query all db one table one"
TABLE_ONE_QUERY_DOC_ONE = "query doc one"
TABLE_TWO_QUERY_ALL = "query all db one table two"

DB_TWO_TABLE_ONE_QUERY_ALL = "query all db two table one"
DB_TWO_TABLE_TWO_QUERY_ALL = "query all db two table two"

ALL_DOCS = "all_docs"
ONLY_DOC_ONE = "only_doc_one"

ACCESSIBLE = "accessible"
INACCESSIBLE = "inaccessible"

MYSQL = {
    TABLE_ONE: {
        TABLE_ONE_QUERY_ALL: [DOC_ONE, DOC_TWO],
        TABLE_ONE_QUERY_DOC_ONE: [DOC_ONE],
    },
    TABLE_TWO: {TABLE_TWO_QUERY_ALL: [DOC_THREE, DOC_FOUR]},
}


def future_with_result(result):
    future = asyncio.Future()
    future.set_result(result)

    return future


@pytest.fixture
def patch_fetch_tables():
    with patch.object(
        MySqlDataSource, "fetch_all_tables", side_effect=([])
    ) as fetch_tables:
        yield fetch_tables


@pytest.fixture
def patch_ping():
    with patch.object(MySqlDataSource, "ping", return_value=AsyncMock()) as ping:
        yield ping


@pytest.fixture
def patch_fetch_rows_for_table():
    with patch.object(MySqlDataSource, "fetch_rows_for_table") as mock_to_patch:
        yield mock_to_patch


@pytest.fixture
def patch_default_wait_multiplier():
    with patch("connectors.sources.mysql.RETRY_INTERVAL", 0):
        yield


@pytest.fixture
def patch_connection_pool():
    connection_pool = Mock()
    connection_pool.close = Mock()
    connection_pool.wait_closed = AsyncMock()
    connection_pool.acquire = Mock(return_value=Connection())
    connection_pool.acquire.__aenter__ = AsyncMock()
    connection_pool.acquire.__aexit__ = AsyncMock()

    with patch(
        "aiomysql.create_pool",
        return_value=future_with_result(connection_pool),
    ):
        yield connection_pool


def test_get_configuration():
    """Test get_configuration method of MySQL"""
    klass = MySqlDataSource

    config = DataSourceConfiguration(klass.get_default_configuration())

    assert config["host"] == "127.0.0.1"
    assert config["port"] == 3306


class Result:
    """This class contains method which returns dummy response"""

    def result(self):
        """Result method which returns dummy result"""
        return [["table1"], ["table2"]]


class Cursor:
    """This class contains methods which returns dummy response"""

    async def __aenter__(self):
        """Make a dummy database connection and return it"""
        return self

    def __init__(self, *args, **kw):
        self.first_call = True
        self.description = [["Database"]]

    def fetchall(self):
        """This method returns object of Return class"""
        futures_object = asyncio.Future()
        futures_object.set_result([["table1"], ["table2"]])
        return futures_object

    async def fetchmany(self, size=1):
        """This method returns response of fetchmany"""
        if self.first_call:
            self.first_call = False
            return [["table1"], ["table2"]]
        if self.is_connection_lost:
            raise Exception("Incomplete Read Error")
        return []

    async def scroll(self, *args, **kw):
        raise Exception("Incomplete Read Error")

    def execute(self, query):
        """This method returns future object"""
        futures_object = asyncio.Future()
        futures_object.set_result(MagicMock())
        return futures_object

    async def __aexit__(self, exception_type, exception_value, exception_traceback):
        """Make sure the dummy database connection gets closed"""
        pass


class Connection:
    """This class contains methods which returns dummy connection response"""

    async def __aenter__(self):
        """Make a dummy database connection and return it"""
        return self

    async def ping(self):
        """This method returns object of Result class"""
        return True

    async def cursor(self):
        """This method returns object of Result class"""
        return Cursor

    async def __aexit__(self, exception_type, exception_value, exception_traceback):
        """Make sure the dummy database connection gets closed"""
        pass


class MockSsl:
    """This class contains methods which returns dummy ssl context"""

    def load_verify_locations(self, cadata):
        """This method verify locations"""
        pass


async def mock_mysql_response():
    """Creates mock response

    Returns:
        Mock Object: Mock response
    """
    mock_response = asyncio.Future()
    mock_response.set_result(MagicMock())

    return mock_response


@pytest.mark.asyncio
async def test_close_when_source_setup_correctly_does_not_raise_errors():
    source = create_source(MySqlDataSource)

    await source.close()


@pytest.mark.asyncio
async def test_ping(patch_logger, patch_connection_pool):
    source = await setup_mysql_source(MySqlDataSource)

    await source.ping()


@pytest.mark.asyncio
async def test_ping_negative(patch_logger):
    source = create_source(MySqlDataSource)

    mock_response = asyncio.Future()
    mock_response.set_result(Mock())

    source.connection_pool = await mock_response

    with patch.object(aiomysql, "create_pool", return_value=mock_response):
        with pytest.raises(Exception):
            await source.ping()


@pytest.mark.asyncio
async def test_connect_with_retry(
    patch_logger, patch_connection_pool, patch_default_wait_multiplier
):
    source = await setup_mysql_source(is_connection_lost=True)

    streamer = source._connect(query="select * from database.table", fetch_many=True)

    with pytest.raises(Exception):
        async for _ in streamer:
            pass


@pytest.mark.asyncio
async def test_fetch_documents(patch_connection_pool):
    source = await setup_mysql_source(DATABASE)

    query = "select * from table"

    response = source._connect(query)

    patch("source._connect", return_value=response)

    document_list = []
    async for document in source.fetch_documents(table="table_name"):
        document_list.append(document)

    assert {
        "Database": f"{DATABASE}",
        "Table": "table_name",
        "_id": f"{DATABASE}_table_name_",
        "_timestamp": "table1",
        f"{DATABASE}_table_name_Database": "table1",
    } in document_list


@pytest.mark.asyncio
async def test_fetch_rows_from_tables(patch_connection_pool):
    source = await setup_mysql_source()

    query = "select * from table"

    response = source._connect(query)

    patch("source._connect", return_value=response)

    async for row in source.fetch_rows_from_tables("table"):
        assert "_id" in row


@pytest.mark.asyncio
async def test_get_docs_with_empty_db_fields_raises_error():
    source = await setup_mysql_source("")

    with pytest.raises(NoDatabaseConfiguredError):
        async for doc, _ in source.get_docs():
            pass


@pytest.mark.asyncio
async def test_get_docs(patch_connection_pool):
    source = await setup_mysql_source(DATABASE)

    source.fetch_rows_from_tables = MagicMock(
        return_value=AsyncIterator([{"a": 1, "b": 2}])
    )

    async for doc, _ in source.get_docs():
        assert doc == {"a": 1, "b": 2}


async def setup_mysql_source(database="", is_connection_lost=False):
    source = create_source(MySqlDataSource)
    source.configuration.set_field(
        name="database", label="Database", value=database, type="str"
    )

    source.database = database

    connection_pool = await mock_mysql_response()
    connection_pool.acquire = Connection
    connection_pool.acquire.cursor = Cursor
    connection_pool.acquire.cursor.is_connection_lost = is_connection_lost

    patch.object(source, "_get_connection_pool", connection_pool)

    return source


def setup_available_docs(advanced_snippet):
    available_docs = []

    for table in advanced_snippet:
        query = advanced_snippet[table]
        available_docs += MYSQL[table][query]

    return available_docs


@pytest.mark.parametrize(
    "filtering, expected_docs",
    [
        (
            # single table, multiple docs
            Filter(
                {
                    ADVANCED_SNIPPET: {
                        "value": {
                            TABLE_ONE: TABLE_ONE_QUERY_ALL,
                        }
                    }
                }
            ),
            {DOC_ONE, DOC_TWO},
        ),
        (
            # single table, single doc
            Filter({ADVANCED_SNIPPET: {"value": {TABLE_ONE: TABLE_ONE_QUERY_DOC_ONE}}}),
            {DOC_ONE},
        ),
        (
            # multiple tables, multiple docs
            Filter(
                {
                    ADVANCED_SNIPPET: {
                        "value": {
                            TABLE_ONE: TABLE_ONE_QUERY_DOC_ONE,
                            TABLE_TWO: TABLE_TWO_QUERY_ALL,
                        }
                    }
                }
            ),
            {DOC_ONE, DOC_THREE, DOC_FOUR},
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_docs_with_advanced_rules(
    filtering, expected_docs, patch_fetch_rows_for_table
):
    source = await setup_mysql_source(DATABASE)
    docs_in_db = setup_available_docs(filtering.get_advanced_rules())
    patch_fetch_rows_for_table.return_value = AsyncIterator(docs_in_db)

    yielded_docs = set()
    async for doc, _ in source.get_docs(filtering):
        yielded_docs.add(doc)

    assert yielded_docs == expected_docs


def test_validate_configuration():
    """This function test _validate_configuration method of MySQL"""
    source = create_source(MySqlDataSource)
    source.configuration.set_field(name="host", value="")

    with pytest.raises(Exception):
        source._validate_configuration()


def test_validate_configuration_with_port():
    """This function test _validate_configuration method with port str input of MySQL"""
    source = create_source(MySqlDataSource)
    source.configuration.set_field(name="port", value="port")

    with pytest.raises(Exception):
        source._validate_configuration()


def test_ssl_context():
    """This function test _ssl_context with dummy certificate"""
    certificate = "-----BEGIN CERTIFICATE----- Certificate -----END CERTIFICATE-----"
    source = create_source(MySqlDataSource)

    with patch.object(ssl, "create_default_context", return_value=MockSsl()):
        source._ssl_context(certificate=certificate)


@pytest.mark.parametrize(
    "datasource, advanced_rules, expected_validation_result",
    [
        (
            {},
            {},
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
        ),
        (
            {TABLE_ONE: {}},
            {TABLE_ONE: {}},
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
        ),
        (
            {TABLE_ONE: {}, TABLE_TWO: {}},
            {TABLE_ONE: {}, TABLE_TWO: {}},
            SyncRuleValidationResult.valid_result(
                SyncRuleValidationResult.ADVANCED_RULES
            ),
        ),
        (
            {},
            {TABLE_ONE: {}},
            SyncRuleValidationResult(
                rule_id=SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=f"Tables not found or inaccessible: {TABLE_ONE}.",
            ),
        ),
        (
            {},
            {TABLE_ONE: {}, TABLE_TWO: {}},
            SyncRuleValidationResult(
                rule_id=SyncRuleValidationResult.ADVANCED_RULES,
                is_valid=False,
                validation_message=f"Tables not found or inaccessible: {TABLE_ONE}, {TABLE_TWO}.",
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_advanced_rules_tables_validation(
    datasource,
    advanced_rules,
    expected_validation_result,
    patch_fetch_tables,
    patch_ping,
):
    patch_fetch_tables.side_effect = [
        map(lambda table: (table, None), datasource.keys())
    ]

    source = create_source(MySqlDataSource)
    validation_result = await MySQLAdvancedRulesValidator(source).validate(
        advanced_rules
    )

    assert validation_result == expected_validation_result


@pytest.mark.parametrize("tables", ["*", ["*"]])
@pytest.mark.asyncio
async def test_get_tables_to_fetch_remote_tables(tables):
    source = create_source(MySqlDataSource)
    source.fetch_all_tables = AsyncMock(return_value="table")

    await source.get_tables_to_fetch()

    assert source.fetch_all_tables.call_count == 1
