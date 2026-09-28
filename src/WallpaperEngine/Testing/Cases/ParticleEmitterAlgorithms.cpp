#include <catch2/catch_test_macros.hpp>

#include "WallpaperEngine/Maths.h"
#include "WallpaperEngine/Render/Objects/ParticleEmitterAlgorithms.h"

#include <cmath>
#include <glm/geometric.hpp>
#include <glm/gtc/constants.hpp>
#include <random>

using WallpaperEngine::Data::Model::ParticleEmitter;
using namespace WallpaperEngine::Render::Objects;

namespace {

uint32_t referenceAccumulateRateEmission (
    float dt, float rate, bool limitOnePerFrame, float& emissionTimer
) {
    emissionTimer += dt * rate;
    uint32_t toEmit = static_cast<uint32_t> (emissionTimer);
    emissionTimer -= static_cast<float> (toEmit);
    if (limitOnePerFrame && toEmit > 1) {
	toEmit = 1;
    }
    return toEmit;
}

glm::vec3 referenceBoxOffset (
    const ParticleEmitter& emitter, const glm::vec3& directions, std::mt19937& rng
) {
    glm::vec3 randomPos;
    for (int axis = 0; axis < 3; axis++) {
	float minDist = emitter.distanceMin[axis];
	float maxDist = emitter.distanceMax[axis];
	float dist = WallpaperEngine::Maths::randomFloat (rng, minDist, maxDist);
	if (WallpaperEngine::Maths::randomFloat (rng, 0.0f, 1.0f) < 0.5f) {
	    dist = -dist;
	}
	randomPos[axis] = dist;
    }
    randomPos *= directions;
    return randomPos;
}

glm::vec3 referenceSphereOffset (const ParticleEmitter& emitter, uint32_t particleFlags, std::mt19937& rng) {
    glm::vec3 randomPos;

    if ((particleFlags & 4) == 0) {
	float angle = WallpaperEngine::Maths::randomFloat (rng, 0.0f, glm::two_pi<float> ());
	float minRadius = emitter.distanceMin.x;
	float maxRadius = emitter.distanceMax.x;
	float minRadiusSq = minRadius * minRadius;
	float maxRadiusSq = maxRadius * maxRadius;
	float radiusXY
	    = std::sqrt (WallpaperEngine::Maths::randomFloat (rng, minRadiusSq, maxRadiusSq));

	randomPos = glm::vec3 (
	    radiusXY * std::cos (angle), radiusXY * std::sin (angle),
	    WallpaperEngine::Maths::randomFloat (rng, -maxRadius, maxRadius)
	);
	randomPos *= emitter.directions;
    } else {
	float theta = WallpaperEngine::Maths::randomFloat (rng, 0.0f, glm::two_pi<float> ());
	float cosTheta = WallpaperEngine::Maths::randomFloat (rng, -1.0f, 1.0f);
	float sinTheta = std::sqrt (1.0f - cosTheta * cosTheta);
	randomPos = glm::vec3 (sinTheta * std::cos (theta), sinTheta * std::sin (theta), cosTheta);

	float minRadius = emitter.distanceMin.x;
	float maxRadius = emitter.distanceMax.x;
	float minRadiusCubed = minRadius * minRadius * minRadius;
	float maxRadiusCubed = maxRadius * maxRadius * maxRadius;
	float radius
	    = std::cbrt (WallpaperEngine::Maths::randomFloat (rng, minRadiusCubed, maxRadiusCubed));
	randomPos *= radius;
	randomPos *= emitter.directions;
    }

    for (int axis = 0; axis < 3; axis++) {
	if (emitter.sign[axis] == 1) {
	    randomPos[axis] = std::abs (randomPos[axis]);
	} else if (emitter.sign[axis] == -1) {
	    randomPos[axis] = -std::abs (randomPos[axis]);
	}
    }

    return randomPos;
}

glm::vec3 referenceVelocity (const ParticleEmitter& emitter, const glm::vec3& randomPos, std::mt19937& rng) {
    if (emitter.speedMax > 0.0f || emitter.speedMin != 0.0f) {
	glm::vec3 direction
	    = glm::length (randomPos) > 0.0f ? glm::normalize (randomPos) : glm::vec3 (0.0f, 1.0f, 0.0f);
	float speed = WallpaperEngine::Maths::randomFloat (rng, emitter.speedMin, emitter.speedMax);
	return direction * speed;
    }
    return glm::vec3 (0.0f);
}

void checkExactVec (const glm::vec3& actual, const glm::vec3& expected) {
    CHECK (actual.x == expected.x);
    CHECK (actual.y == expected.y);
    CHECK (actual.z == expected.z);
}

ParticleEmitter makeEmitter () {
    ParticleEmitter emitter {};
    emitter.directions = glm::vec3 (0.75f, -1.5f, 2.25f);
    emitter.distanceMin = glm::vec3 (0.2f, 0.5f, 1.0f);
    emitter.distanceMax = glm::vec3 (3.0f, 4.0f, 5.0f);
    emitter.sign = glm::ivec3 (1, -1, 0);
    emitter.speedMin = 1.25f;
    emitter.speedMax = 8.5f;
    return emitter;
}

} // namespace

TEST_CASE ("Particle rate accumulation remains bit-for-bit equivalent to the pre-refactor logic") {
    float actualTimer = 0.375f;
    float referenceTimer = actualTimer;

    const float deltas[] = { 0.016f, 0.033f, 0.1f, 0.005f, 0.25f };
    for (int pass = 0; pass < 40; pass++) {
	for (float dt : deltas) {
	    const bool limitOnePerFrame = (pass % 3) == 0;
	    const float rate = 37.25f + static_cast<float> (pass);

	    const uint32_t expected
		= referenceAccumulateRateEmission (dt, rate, limitOnePerFrame, referenceTimer);
	    const uint32_t actual
		= ParticleEmitterAlgorithms::accumulateRateEmission (dt, rate, limitOnePerFrame, actualTimer);

	    CHECK (actual == expected);
	    CHECK (actualTimer == referenceTimer);
	}
    }
}

TEST_CASE ("Box emitter sampling preserves the old random stream and coordinates") {
    const ParticleEmitter emitter = makeEmitter ();
    const glm::vec3 directions (1.0f, -2.0f, 0.5f);
    std::mt19937 actualRng (0xC0FFEEu);
    std::mt19937 referenceRng (0xC0FFEEu);

    for (int sample = 0; sample < 512; sample++) {
	const glm::vec3 expected = referenceBoxOffset (emitter, directions, referenceRng);
	const glm::vec3 actual = ParticleEmitterAlgorithms::sampleBoxOffset (emitter, directions, actualRng);

	checkExactVec (actual, expected);
	CHECK (actualRng == referenceRng);
    }
}

TEST_CASE ("Sphere emitter sampling preserves orthographic behavior and RNG ordering") {
    const ParticleEmitter emitter = makeEmitter ();
    std::mt19937 actualRng (1234567u);
    std::mt19937 referenceRng (1234567u);

    for (int sample = 0; sample < 512; sample++) {
	const glm::vec3 expected = referenceSphereOffset (emitter, 0, referenceRng);
	const glm::vec3 actual = ParticleEmitterAlgorithms::sampleSphereOffset (emitter, 0, actualRng);

	checkExactVec (actual, expected);
	CHECK (actualRng == referenceRng);
    }
}

TEST_CASE ("Sphere emitter sampling preserves perspective behavior and RNG ordering") {
    const ParticleEmitter emitter = makeEmitter ();
    std::mt19937 actualRng (7654321u);
    std::mt19937 referenceRng (7654321u);

    for (int sample = 0; sample < 512; sample++) {
	const glm::vec3 expected = referenceSphereOffset (emitter, 4, referenceRng);
	const glm::vec3 actual = ParticleEmitterAlgorithms::sampleSphereOffset (emitter, 4, actualRng);

	checkExactVec (actual, expected);
	CHECK (actualRng == referenceRng);
    }
}

TEST_CASE ("Emitter velocity preserves speed sampling and zero-offset fallback") {
    ParticleEmitter emitter = makeEmitter ();
    std::mt19937 actualRng (998877u);
    std::mt19937 referenceRng (998877u);

    for (const glm::vec3 offset : { glm::vec3 (2.0f, -3.0f, 4.0f), glm::vec3 (0.0f) }) {
	const glm::vec3 expected = referenceVelocity (emitter, offset, referenceRng);
	const glm::vec3 actual = ParticleEmitterAlgorithms::resolveVelocity (emitter, offset, actualRng);

	checkExactVec (actual, expected);
	CHECK (actualRng == referenceRng);
    }

    emitter.speedMin = 0.0f;
    emitter.speedMax = 0.0f;
    const std::mt19937 before = actualRng;
    checkExactVec (
	ParticleEmitterAlgorithms::resolveVelocity (emitter, glm::vec3 (1.0f), actualRng), glm::vec3 (0.0f)
    );
    CHECK (actualRng == before);
}
