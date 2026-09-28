#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Data/Parsers/ObjectParser.h"

using WallpaperEngine::Data::JSON::JSON;
using WallpaperEngine::Data::Model::CameraObject;
using WallpaperEngine::Data::Model::Light;
using WallpaperEngine::Data::Model::Project;
using WallpaperEngine::Data::Parsers::ObjectParser;

TEST_CASE ("Object parser preserves numeric names through the base-data fallback") {
    Project project {};
    const JSON objectData = {
	{ "id", 7 },
	{ "name", 42 },
	{ "solid", true },
    };

    const auto object = ObjectParser::parse (objectData, project);

    REQUIRE (object != nullptr);
    CHECK (object->id == 7);
    CHECK (object->name == "42");
}

TEST_CASE ("Object parser dispatches camera objects without loading assets") {
    Project project {};
    const JSON objectData = {
	{ "id", 8 },
	{ "name", "camera-object" },
	{ "camera", "main-camera" },
    };

    const auto object = ObjectParser::parse (objectData, project);

    REQUIRE (object != nullptr);
    REQUIRE (object->is<CameraObject> ());
    const auto* camera = object->as<CameraObject> ();
    CHECK (camera->camera == "main-camera");
    CHECK (camera->path.empty ());
    CHECK (camera->queueMode == "random");
}

TEST_CASE ("Object parser keeps invalid light objects as generic objects") {
    Project project {};
    const JSON objectData = {
	{ "id", 9 },
	{ "name", "invalid-light" },
	{ "light", 123 },
    };

    const auto object = ObjectParser::parse (objectData, project);

    REQUIRE (object != nullptr);
    CHECK_FALSE (object->is<Light> ());
    CHECK (object->id == 9);
    CHECK (object->name == "invalid-light");
}
