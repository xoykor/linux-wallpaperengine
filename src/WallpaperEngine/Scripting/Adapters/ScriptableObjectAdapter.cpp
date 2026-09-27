#include "ScriptableObjectAdapter.h"

#include <utility>

#include "WallpaperEngine/Data/Utils/ScopeGuard.h"
#include "WallpaperEngine/Logging/Log.h"
#include "WallpaperEngine/Scripting/ScriptEngine.h"
#include "WallpaperEngine/Scripting/ScriptableObject.h"

#include <string>

using namespace WallpaperEngine::Data::Model;
using namespace WallpaperEngine::Data::Utils;
using namespace WallpaperEngine::Scripting::Adapters;

#define SCRIPTABLE_OPAQUE_MAGIC 0xdeadbeef

namespace {
bool updateDynamicValue (
    JSContext* ctx, JSValueConst value, DynamicValue& target, const bool degreesToRadians = false
) {
    const int tag = JS_VALUE_GET_TAG (value);

    if (tag == JS_TAG_UNDEFINED || tag == JS_TAG_UNINITIALIZED || tag == JS_TAG_NULL) {
	target.update (DynamicValue::UpdateSource::Script);
	return true;
    }
    if (tag == JS_TAG_INT) {
	target.update (JS_VALUE_GET_INT (value), DynamicValue::UpdateSource::Script);
	return true;
    }
    if (tag == JS_TAG_BOOL) {
	target.update (JS_VALUE_GET_BOOL (value) != 0, DynamicValue::UpdateSource::Script);
	return true;
    }
    if (JS_TAG_IS_FLOAT64 (tag)) {
	target.update (static_cast<float> (JS_VALUE_GET_FLOAT64 (value)), DynamicValue::UpdateSource::Script);
	return true;
    }
    if (tag == JS_TAG_STRING) {
	const char* text = JS_ToCString (ctx, value);
	if (text == nullptr) {
	    return false;
	}
	target.update (std::string (text), DynamicValue::UpdateSource::Script);
	JS_FreeCString (ctx, text);
	return true;
    }
    if (tag != JS_TAG_OBJECT) {
	return false;
    }

    JSValue x = JS_GetPropertyStr (ctx, value, "x");
    JSValue y = JS_GetPropertyStr (ctx, value, "y");
    JSValue z = JS_GetPropertyStr (ctx, value, "z");
    JSValue w = JS_GetPropertyStr (ctx, value, "w");
    ScopeGuard guard ([=] {
	JS_FreeValue (ctx, x);
	JS_FreeValue (ctx, y);
	JS_FreeValue (ctx, z);
	JS_FreeValue (ctx, w);
    });

    if (!JS_IsNumber (x) || !JS_IsNumber (y)) {
	JS_ThrowTypeError (ctx, "Layer vector properties require numeric x and y components");
	return false;
    }

    double xValue = 0.0;
    double yValue = 0.0;
    double zValue = 0.0;
    double wValue = 0.0;
    if (JS_ToFloat64 (ctx, &xValue, x) < 0 || JS_ToFloat64 (ctx, &yValue, y) < 0) {
	return false;
    }
    if (JS_IsNumber (z) && JS_ToFloat64 (ctx, &zValue, z) < 0) {
	return false;
    }
    if (JS_IsNumber (w) && JS_ToFloat64 (ctx, &wValue, w) < 0) {
	return false;
    }

    switch (target.getType ()) {
	case DynamicValue::Vec2:
	    target.update (glm::vec2 (xValue, yValue), DynamicValue::UpdateSource::Script);
	    return true;
	case DynamicValue::Vec3:
	    if (!JS_IsNumber (z)) {
		JS_ThrowTypeError (ctx, "Layer Vec3 properties require a numeric z component");
		return false;
	    }
	    target.update (
		degreesToRadians ? glm::vec3 (xValue, yValue, zValue) * 0.017453292519943295769f
				 : glm::vec3 (xValue, yValue, zValue),
		DynamicValue::UpdateSource::Script
	    );
	    return true;
	case DynamicValue::Vec4:
	    if (!JS_IsNumber (z) || !JS_IsNumber (w)) {
		JS_ThrowTypeError (ctx, "Layer Vec4 properties require numeric z and w components");
		return false;
	    }
	    target.update (glm::vec4 (xValue, yValue, zValue, wValue), DynamicValue::UpdateSource::Script);
	    return true;
	default:
	    JS_ThrowTypeError (ctx, "Layer property type cannot be assigned a vector");
	    return false;
    }
}
} // namespace

struct OpaqueScriptableObjectAdapter {
    unsigned int magic;
    ScriptableObjectAdapter& adapter;
    WallpaperEngine::Scripting::ScriptableObject& object;
};

JSValue scriptableobject_property_get (JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst receiver) {
    JSClassID classId = 0;

    auto* container = static_cast<OpaqueScriptableObjectAdapter*> (JS_GetAnyOpaque (obj_val, &classId));

    if (!container || container->magic != SCRIPTABLE_OPAQUE_MAGIC) {
	return JS_ThrowTypeError (ctx, "Invalid scriptable layer receiver while reading a property");
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return JS_EXCEPTION;
    }

    ScopeGuard guard ([=] { JS_FreeCString (ctx, name); });

    try {
	// find the property inside, otherwise return undefined
	auto& property = container->object.getProperty (name);
	JSValue result = std::strcmp (name, "angles") == 0 ? container->adapter.getEngine ().anglesToJs (property)
							   : container->adapter.getEngine ().dynamicToJs (property);
	return result;
    } catch (const std::exception& e) {
	return JS_UNDEFINED;
    }
}

int scriptableobject_property_set (
    JSContext* ctx, JSValueConst obj_val, JSAtom atom, JSValueConst val, JSValueConst receiver, int flags
) {
    JSClassID classId = 0;

    auto* container = static_cast<OpaqueScriptableObjectAdapter*> (JS_GetAnyOpaque (obj_val, &classId));

    if (!container || container->magic != SCRIPTABLE_OPAQUE_MAGIC) {
	JS_ThrowTypeError (ctx, "Invalid scriptable layer receiver while setting a property");
	return -1;
    }

    const char* name = JS_AtomToCString (ctx, atom);

    if (name == nullptr) {
	return -1;
    }

    ScopeGuard guard ([=] { JS_FreeCString (ctx, name); });
    try {
	auto& property = container->object.getProperty (name);
	if (updateDynamicValue (ctx, val, property, std::strcmp (name, "angles") == 0)) {
	    return 1;
	}
	if (!JS_HasException (ctx)) {
	    JS_ThrowTypeError (ctx, "Unsupported value for scriptable layer property '%s'", name);
	}
	return -1;
    } catch (const std::exception&) {
	JS_ThrowTypeError (ctx, "Unknown scriptable layer property '%s'", name);
	return -1;
    }
}

ScriptableObjectAdapter::ScriptableObjectAdapter (ScriptEngine& engine, std::string name) :
    ObjectAdapter (engine), m_exoticMethods (), m_name (std::move (name)) {
    this->m_exoticMethods = {
	.get_property = scriptableobject_property_get,
	.set_property = scriptableobject_property_set,
    };
    this->registerType (
	{
	    .class_name = m_name.c_str (),
	    .exotic = &m_exoticMethods,
	}
    );
}

JSValue ScriptableObjectAdapter::instantiate (ScriptableObject& object) {
    JSValue result = this->ObjectAdapter::instantiate (object);
    JS_SetOpaque (
	result,
	new OpaqueScriptableObjectAdapter { .magic = SCRIPTABLE_OPAQUE_MAGIC, .adapter = *this, .object = object }
    );

    return result;
}

JSValue ScriptableObjectAdapter::instantiate (DynamicValue& value) {
    throw std::runtime_error ("Cannot create a ScriptableObject instance from a DynamicValue");
}
