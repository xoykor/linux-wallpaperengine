#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Render/Objects/Effects/TexturePipelineOrder.h"

#include <map>
#include <string>
#include <vector>

using namespace WallpaperEngine::Render::Objects::Effects;

namespace {

void prepend (std::map<int, std::vector<std::string>>& chains, int index, std::string value) {
    chains[index].insert (chains[index].begin (), std::move (value));
}

} // namespace

TEST_CASE ("Texture pipeline preserves the pre-refactor precedence order") {
    std::vector<std::string> stages;
    std::map<int, std::vector<std::string>> chains;

    TexturePipelineOrder::apply (
	[&] {
	    stages.emplace_back ("vertex");
	    chains[0] = { "vertex" };
	},
	[&] {
	    stages.emplace_back ("fragment");
	    prepend (chains, 0, "fragment");
	},
	[&] {
	    stages.emplace_back ("pass");
	    prepend (chains, 0, "pass");
	},
	[&] {
	    stages.emplace_back ("pass-user");
	    prepend (chains, 0, "pass-user");
	},
	[&] {
	    stages.emplace_back ("override");
	    prepend (chains, 0, "override");
	},
	[&] {
	    stages.emplace_back ("override-user");
	    prepend (chains, 0, "override-user");
	},
	[&] {
	    stages.emplace_back ("bind");
	    prepend (chains, 0, "bind");
	}
    );

    const std::vector<std::string> expectedStages
	= { "vertex", "fragment", "pass", "pass-user", "override", "override-user", "bind" };
    const std::vector<std::string> expectedChain
	= { "bind", "override-user", "override", "pass-user", "pass", "fragment", "vertex" };

    CHECK (stages == expectedStages);
    CHECK (chains.at (0) == expectedChain);
}

TEST_CASE ("Texture pipeline does not disturb independent texture slots") {
    std::map<int, std::vector<std::string>> chains;

    TexturePipelineOrder::apply (
	[&] {
	    chains[0] = { "vertex-0" };
	    chains[1] = { "vertex-1" };
	},
	[&] { prepend (chains, 1, "fragment-1"); }, [&] { prepend (chains, 0, "pass-0"); }, [&] { },
	[&] { prepend (chains, 1, "override-1"); }, [&] { }, [&] { prepend (chains, 0, "bind-0"); }
    );

    const std::vector<std::string> expectedSlot0 = { "bind-0", "pass-0", "vertex-0" };
    const std::vector<std::string> expectedSlot1 = { "override-1", "fragment-1", "vertex-1" };
    CHECK (chains.at (0) == expectedSlot0);
    CHECK (chains.at (1) == expectedSlot1);
}
