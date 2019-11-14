import inspect
import logging
import sys
import textwrap
from collections import OrderedDict
from decimal import Decimal

from django.db import models
from django.utils.encoding import force_str
from rest_framework import serializers, status
from rest_framework.mixins import DestroyModelMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.parsers import FileUploadParser
from rest_framework.request import is_form_media_type
from rest_framework.settings import api_settings as rest_framework_settings
from rest_framework.utils import encoders, json
from rest_framework.views import APIView

from .app_settings import swagger_settings

logger = logging.getLogger(__name__)


class no_body(object):
    """Used as a sentinel value to forcibly remove the body of a request via :func:`.swagger_auto_schema`."""
    pass


class unset(object):
    """Used as a sentinel value for function parameters not set by the caller where ``None`` would be a valid value."""
    pass


def swagger_auto_schema(method=None, methods=None, auto_schema=unset, request_body=None, query_serializer=None,
                        manual_parameters=None, operation_id=None, operation_description=None, operation_summary=None,
                        security=None, deprecated=None, responses=None, field_inspectors=None, filter_inspectors=None,
                        paginator_inspectors=None, tags=None, **extra_overrides):
    """Decorate a view method to customize the :class:`.Operation` object generated from it.

    `method` and `methods` are mutually exclusive and must only be present when decorating a view method that accepts
    more than one HTTP request method.

    The `auto_schema` and `operation_description` arguments take precendence over view- or method-level values.

    :param str method: for multi-method views, the http method the options should apply to
    :param list[str] methods: for multi-method views, the http methods the options should apply to
    :param drf_yasg.inspectors.SwaggerAutoSchema auto_schema: custom class to use for generating the Operation object;
        this overrides both the class-level ``swagger_schema`` attribute and the ``DEFAULT_AUTO_SCHEMA_CLASS``
        setting, and can be set to ``None`` to prevent this operation from being generated
    :param request_body: custom request body which will be used as the ``schema`` property of a
        :class:`.Parameter` with ``in: 'body'``.

        A Schema or SchemaRef is not valid if this request consumes form-data, because ``form`` and ``body`` parameters
        are mutually exclusive in an :class:`.Operation`. If you need to set custom ``form`` parameters, you can use
        the `manual_parameters` argument.

        If a ``Serializer`` class or instance is given, it will be automatically converted into a :class:`.Schema`
        used as a ``body`` :class:`.Parameter`, or into a list of ``form`` :class:`.Parameter`\\ s, as appropriate.
    :type request_body: drf_yasg.openapi.Schema or drf_yasg.openapi.SchemaRef  or rest_framework.serializers.Serializer
        or type[no_body]

    :param rest_framework.serializers.Serializer query_serializer: if you use a ``Serializer`` to parse query
        parameters, you can pass it here and have :class:`.Parameter` objects be generated automatically from it.

        If any ``Field`` on the serializer cannot be represented as a ``query`` :class:`.Parameter`
        (e.g. nested Serializers, file fields, ...), the schema generation will fail with an error.

        Schema generation will also fail if the name of any Field on the `query_serializer` conflicts with parameters
        generated by ``filter_backends`` or ``paginator``.

    :param list[drf_yasg.openapi.Parameter] manual_parameters: a list of manual parameters to override the
        automatically generated ones

        :class:`.Parameter`\\ s are identified by their (``name``, ``in``) combination, and any parameters given
        here will fully override automatically generated parameters if they collide.

        It is an error to supply ``form`` parameters when the request does not consume form-data.

    :param str operation_id: operation ID override; the operation ID must be unique accross the whole API
    :param str operation_description: operation description override
    :param str operation_summary: operation summary string
    :param list[dict] security: security requirements override; used to specify which authetication mechanism
        is requried to call this API; an empty list marks the endpoint as unauthenticated (i.e. removes all accepted
        authentication schemes), and ``None`` will inherit the top-level secuirty requirements
    :param bool deprecated: deprecation status for operation
    :param responses: a dict of documented manual responses
        keyed on response status code. If no success (``2xx``) response is given, one will automatically be
        generated from the request body and http method. If any ``2xx`` response is given the automatic response is
        suppressed.

        * if a plain string is given as value, a :class:`.Response` with no body and that string as its description
          will be generated
        * if ``None`` is given as a value, the response is ignored; this is mainly useful for disabling default
          2xx responses, i.e. ``responses={200: None, 302: 'something'}``
        * if a :class:`.Schema`, :class:`.SchemaRef` is given, a :class:`.Response` with the schema as its body and
          an empty description will be generated
        * a ``Serializer`` class or instance will be converted into a :class:`.Schema` and treated as above
        * a :class:`.Response` object will be used as-is; however if its ``schema`` attribute is a ``Serializer``,
          it will automatically be converted into a :class:`.Schema`
    :type responses: dict[int or str, (drf_yasg.openapi.Schema or drf_yasg.openapi.SchemaRef or
        drf_yasg.openapi.Response or str or rest_framework.serializers.Serializer)]

    :param list[type[drf_yasg.inspectors.FieldInspector]] field_inspectors: extra serializer and field inspectors; these
        will be tried before :attr:`.ViewInspector.field_inspectors` on the :class:`.inspectors.SwaggerAutoSchema`
    :param list[type[drf_yasg.inspectors.FilterInspector]] filter_inspectors: extra filter inspectors; these will be
        tried before :attr:`.ViewInspector.filter_inspectors` on the :class:`.inspectors.SwaggerAutoSchema`
    :param list[type[drf_yasg.inspectors.PaginatorInspector]] paginator_inspectors: extra paginator inspectors; these
        will be tried before :attr:`.ViewInspector.paginator_inspectors` on the :class:`.inspectors.SwaggerAutoSchema`
    :param list[str] tags: tags override
    :param extra_overrides: extra values that will be saved into the ``overrides`` dict; these values will be available
        in the handling :class:`.inspectors.SwaggerAutoSchema` instance via ``self.overrides``
    """

    def decorator(view_method):
        assert not any(hm in extra_overrides for hm in APIView.http_method_names), "HTTP method names not allowed here"
        data = {
            'request_body': request_body,
            'query_serializer': query_serializer,
            'manual_parameters': manual_parameters,
            'operation_id': operation_id,
            'operation_summary': operation_summary,
            'deprecated': deprecated,
            'operation_description': operation_description,
            'security': security,
            'responses': responses,
            'filter_inspectors': list(filter_inspectors) if filter_inspectors else None,
            'paginator_inspectors': list(paginator_inspectors) if paginator_inspectors else None,
            'field_inspectors': list(field_inspectors) if field_inspectors else None,
            'tags': list(tags) if tags else None,
        }
        data = filter_none(data)
        if auto_schema is not unset:
            data['auto_schema'] = auto_schema
        data.update(extra_overrides)
        if not data:  # pragma: no cover
            # no overrides to set, no use in doing more work
            return view_method

        # if the method is an @action, it will have a bind_to_methods attribute, or a mapping attribute for drf>3.8
        bind_to_methods = getattr(view_method, 'bind_to_methods', [])
        mapping = getattr(view_method, 'mapping', {})
        mapping_methods = [mth for mth, name in mapping.items() if name == view_method.__name__]
        action_http_methods = bind_to_methods + mapping_methods

        # if the method is actually a function based view (@api_view), it will have a 'cls' attribute
        view_cls = getattr(view_method, 'cls', None)
        api_view_http_methods = [m for m in getattr(view_cls, 'http_method_names', []) if hasattr(view_cls, m)]

        available_http_methods = api_view_http_methods + action_http_methods
        existing_data = getattr(view_method, '_swagger_auto_schema', {})

        _methods = methods
        if methods or method:
            assert available_http_methods, "`method` or `methods` can only be specified on @action or @api_view views"
            assert bool(methods) != bool(method), "specify either method or methods"
            assert not isinstance(methods, str), "`methods` expects to receive a list of methods;" \
                                                 " use `method` for a single argument"
            if method:
                _methods = [method.lower()]
            else:
                _methods = [mth.lower() for mth in methods]
            assert all(mth in available_http_methods for mth in _methods), "http method not bound to view"
            assert not any(mth in existing_data for mth in _methods), "http method defined multiple times"

        if available_http_methods:
            # action or api_view
            assert bool(api_view_http_methods) != bool(action_http_methods), "this should never happen"

            if len(available_http_methods) > 1:
                assert _methods, \
                    "on multi-method api_view or action, you must specify " \
                    "swagger_auto_schema on a per-method basis using one of the `method` or `methods` arguments"
            else:
                # for a single-method view we assume that single method as the decorator target
                _methods = _methods or available_http_methods

            assert not any(hasattr(getattr(view_cls, mth, None), '_swagger_auto_schema') for mth in _methods), \
                "swagger_auto_schema applied twice to method"
            assert not any(mth in existing_data for mth in _methods), "swagger_auto_schema applied twice to method"
            existing_data.update((mth.lower(), data) for mth in _methods)
            view_method._swagger_auto_schema = existing_data
        else:
            assert not _methods, \
                "the methods argument should only be specified when decorating an action; " \
                "you should also ensure that you put the swagger_auto_schema decorator " \
                "AFTER (above) the _route decorator"
            assert not existing_data, "swagger_auto_schema applied twice to method"
            view_method._swagger_auto_schema = data

        return view_method

    return decorator


def swagger_serializer_method(serializer_or_field):
    """
    Decorates the method of a serializers.SerializerMethodField
    to hint as to how Swagger should be generated for this field.

    :param serializer_or_field: ``Serializer``/``Field`` class or instance
    :return:
    """

    def decorator(serializer_method):
        # stash the serializer for SerializerMethodFieldInspector to find
        serializer_method._swagger_serializer = serializer_or_field
        return serializer_method

    return decorator


def is_list_view(path, method, view):
    """Check if the given path/method appears to represent a list view (as opposed to a detail/instance view).

    :param str path: view path
    :param str method: http method
    :param APIView view: target view
    :rtype: bool
    """
    # for ViewSets, it could be the default 'list' action, or an @action(detail=False)
    action = getattr(view, 'action', '')
    method = getattr(view, action, None) or method
    detail = getattr(method, 'detail', None)
    suffix = getattr(view, 'suffix', None)
    if action in ('list', 'create') or detail is False or suffix == 'List':
        return True

    if action in ('retrieve', 'update', 'partial_update', 'destroy') or detail is True or suffix == 'Instance':
        # a detail action is surely not a list route
        return False

    if isinstance(view, ListModelMixin):
        return True

    # for GenericAPIView, if it's a detail view it can't also be a list view
    if isinstance(view, (RetrieveModelMixin, UpdateModelMixin, DestroyModelMixin)):
        return False

    # if the last component in the path is parameterized it's probably not a list view
    path_components = path.strip('/').split('/')
    if path_components and '{' in path_components[-1]:
        return False

    # otherwise assume it's a list view
    return True


def guess_response_status(method):
    if method == 'post':
        return status.HTTP_201_CREATED
    elif method == 'delete':
        return status.HTTP_204_NO_CONTENT
    else:
        return status.HTTP_200_OK


def param_list_to_odict(parameters):
    """Transform a list of :class:`.Parameter` objects into an ``OrderedDict`` keyed on the ``(name, in_)`` tuple of
    each parameter.

    Raises an ``AssertionError`` if `parameters` contains duplicate parameters (by their name + in combination).

    :param list[drf_yasg.openapi.Parameter] parameters: the list of parameters
    :return: `parameters` keyed by ``(name, in_)``
    :rtype: dict[(str,str),drf_yasg.openapi.Parameter]
    """
    result = OrderedDict(((param.name, param.in_), param) for param in parameters)
    assert len(result) == len(parameters), "duplicate Parameters found"
    return result


def merge_params(parameters, overrides):
    """Merge `overrides` into `parameters`. This is the same as appending `overrides` to `parameters`, but any element
    of `parameters` whose ``(name, in_)`` tuple collides with an element in `overrides` is replaced by it.

    Raises an ``AssertionError`` if either list contains duplicate parameters.

    :param list[drf_yasg.openapi.Parameter] parameters: initial parameters
    :param list[drf_yasg.openapi.Parameter] overrides: overriding parameters
    :return: merged list
    :rtype: list[drf_yasg.openapi.Parameter]
    """
    parameters = param_list_to_odict(parameters)
    parameters.update(param_list_to_odict(overrides))
    return list(parameters.values())


def filter_none(obj):
    """Remove ``None`` values from tuples, lists or dictionaries. Return other objects as-is.

    :param obj: the object
    :return: collection with ``None`` values removed
    """
    if obj is None:
        return None
    new_obj = None
    if isinstance(obj, dict):
        new_obj = type(obj)((k, v) for k, v in obj.items() if k is not None and v is not None)
    if isinstance(obj, (list, tuple)):
        new_obj = type(obj)(v for v in obj if v is not None)
    if new_obj is not None and len(new_obj) != len(obj):
        return new_obj  # pragma: no cover
    return obj


def force_serializer_instance(serializer):
    """Force `serializer` into a ``Serializer`` instance. If it is not a ``Serializer`` class or instance, raises
    an assertion error.

    :param serializer: serializer class or instance
    :type serializer: serializers.BaseSerializer or type[serializers.BaseSerializer]
    :return: serializer instance
    :rtype: serializers.BaseSerializer
    """
    if inspect.isclass(serializer):
        assert issubclass(serializer, serializers.BaseSerializer), "Serializer required, not %s" % serializer.__name__
        return serializer()

    assert isinstance(serializer, serializers.BaseSerializer), \
        "Serializer class or instance required, not %s" % type(serializer).__name__
    return serializer


def get_serializer_class(serializer):
    """Given a ``Serializer`` class or intance, return the ``Serializer`` class. If `serializer` is not a ``Serializer``
    class or instance, raises an assertion error.

    :param serializer: serializer class or instance, or ``None``
    :return: serializer class
    :rtype: type[serializers.BaseSerializer]
    """
    if serializer is None:
        return None

    if inspect.isclass(serializer):
        assert issubclass(serializer, serializers.BaseSerializer), "Serializer required, not %s" % serializer.__name__
        return serializer

    assert isinstance(serializer, serializers.BaseSerializer), \
        "Serializer class or instance required, not %s" % type(serializer).__name__
    return type(serializer)


def get_object_classes(classes_or_instances, expected_base_class=None):
    """Given a list of instances or class objects, return the list of their classes.

    :param classes_or_instances: mixed list to parse
    :type classes_or_instances: list[type or object]
    :param expected_base_class: if given, only subclasses or instances of this type will be returned
    :type expected_base_class: type
    :return: list of classes
    :rtype: list
    """
    classes_or_instances = classes_or_instances or []
    result = []
    for obj in classes_or_instances:
        if inspect.isclass(obj):
            if not expected_base_class or issubclass(obj, expected_base_class):
                result.append(obj)
        else:
            if not expected_base_class or isinstance(obj, expected_base_class):
                result.append(type(obj))

    return result


def get_consumes(parser_classes):
    """Extract ``consumes`` MIME types from a list of parser classes.

    :param list parser_classes: parser classes
    :type parser_classes: list[rest_framework.parsers.BaseParser or type[rest_framework.parsers.BaseParser]]
    :return: MIME types for ``consumes``
    :rtype: list[str]
    """
    parser_classes = get_object_classes(parser_classes)
    parser_classes = [pc for pc in parser_classes if not issubclass(pc, FileUploadParser)]
    media_types = [parser.media_type for parser in parser_classes or []]
    non_form_media_types = [encoding for encoding in media_types if not is_form_media_type(encoding)]
    if len(non_form_media_types) == 0:
        return media_types
    else:
        return non_form_media_types


def get_produces(renderer_classes):
    """Extract ``produces`` MIME types from a list of renderer classes.

    :param list renderer_classes: renderer classes
    :type renderer_classes: list[rest_framework.renderers.BaseRenderer or type[rest_framework.renderers.BaseRenderer]]
    :return: MIME types for ``produces``
    :rtype: list[str]
    """
    renderer_classes = get_object_classes(renderer_classes)
    media_types = [renderer.media_type for renderer in renderer_classes or []]
    media_types = [encoding for encoding in media_types
                   if not any(excluded in encoding for excluded in swagger_settings.EXCLUDED_MEDIA_TYPES)]
    return media_types


def decimal_as_float(field):
    """Returns true if ``field`` is a django-rest-framework DecimalField and its ``coerce_to_string`` attribute or the
    ``COERCE_DECIMAL_TO_STRING`` setting is set to ``False``.

    :rtype: bool
    """
    if isinstance(field, serializers.DecimalField) or isinstance(field, models.DecimalField):
        return not getattr(field, 'coerce_to_string', rest_framework_settings.COERCE_DECIMAL_TO_STRING)
    return False


def get_serializer_ref_name(serializer):
    """Get serializer's ref_name (or None for ModelSerializer if it is named 'NestedSerializer')

    :param serializer: Serializer instance
    :return: Serializer's ``ref_name`` or ``None`` for inline serializer
    :rtype: str or None
    """
    serializer_meta = getattr(serializer, 'Meta', None)
    serializer_name = type(serializer).__name__
    if hasattr(serializer_meta, 'ref_name'):
        ref_name = serializer_meta.ref_name
    elif serializer_name == 'NestedSerializer' and isinstance(serializer, serializers.ModelSerializer):
        logger.debug("Forcing inline output for ModelSerializer named 'NestedSerializer':\n" + str(serializer))
        ref_name = None
    else:
        ref_name = serializer_name
        if ref_name.endswith('Serializer'):
            ref_name = ref_name[:-len('Serializer')]
    return ref_name


def force_real_str(s, encoding='utf-8', strings_only=False, errors='strict'):
    """
    Force `s` into a ``str`` instance.

    Fix for https://github.com/axnsan12/drf-yasg/issues/159
    """
    if s is not None:
        s = force_str(s, encoding, strings_only, errors)
        if type(s) != str:
            s = '' + s

        # Remove common indentation to get the correct Markdown rendering
        s = textwrap.dedent(s)

    return s


def field_value_to_representation(field, value):
    """Convert a python value related to a field (default, choices, etc.) into its OpenAPI-compatible representation.

    :param serializers.Field field: field associated with the value
    :param object value: value
    :return: the converted value
    """
    value = field.to_representation(value)
    if isinstance(value, Decimal):
        if decimal_as_float(field):
            value = float(value)
        else:
            value = str(value)

    # JSON roundtrip ensures that the value is valid JSON;
    # for example, sets and tuples get transformed into lists
    return json.loads(json.dumps(value, cls=encoders.JSONEncoder))


def get_field_default(field):
    """
    Get the default value for a field, converted to a JSON-compatible value while properly handling callables.

    :param field: field instance
    :return: default value
    """
    default = getattr(field, 'default', serializers.empty)
    if default is not serializers.empty:
        if callable(default):
            try:
                if hasattr(default, 'set_context'):
                    default.set_context(field)
                default = default()
            except Exception:  # pragma: no cover
                logger.warning("default for %s is callable but it raised an exception when "
                               "called; 'default' will not be set on schema", field, exc_info=True)
                default = serializers.empty

        if default is not serializers.empty and default is not None:
            try:
                default = field_value_to_representation(field, default)
            except Exception:  # pragma: no cover
                logger.warning("'default' on schema for %s will not be set because "
                               "to_representation raised an exception", field, exc_info=True)
                default = serializers.empty

    return default


def dict_has_ordered_keys(obj):
    """Check if a given object is a dict that maintains insertion order.

    :param obj: the dict object to check
    :rtype: bool
    """
    if sys.version_info >= (3, 7):
        # the Python 3.7 language spec says that dict must maintain insertion order.
        return isinstance(obj, dict)

    return isinstance(obj, OrderedDict)
