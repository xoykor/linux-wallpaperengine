#include "CModel.h"

#include "WallpaperEngine/Data/Builders/UserSettingBuilder.h"
#include "WallpaperEngine/Data/Model/Material.h"
#include "WallpaperEngine/Data/Model/Project.h"
#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <glm/gtc/matrix_inverse.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <string_view>

using namespace WallpaperEngine;
using namespace WallpaperEngine::Render::Objects;

CModel::CModel (Wallpapers::CScene& scene, const ModelObject& model) :
    CObject (scene, model), CRenderable (scene, model, *model.material), ScriptableObject (scene, model),
    m_model (model) {
    this->registerProperty ("origin", *model.origin->value);
    this->registerProperty ("scale", *model.scale->value);
    this->registerProperty ("angles", *model.angles->value);
    this->registerProperty ("visible", *model.visible->value);
    this->registerProperty ("alpha", *model.alpha->value);
    this->registerProperty ("color", *model.color->value);

    this->detectTexture ();
}

CModel::~CModel () {
    for (auto& submesh : m_submeshes) {
	delete submesh.pass;
	if (submesh.vbo != GL_NONE) {
	    glDeleteBuffers (1, &submesh.vbo);
	}
	if (submesh.ebo != GL_NONE) {
	    glDeleteBuffers (1, &submesh.ebo);
	}
	if (submesh.vao != GL_NONE) {
	    glDeleteVertexArrays (1, &submesh.vao);
	}
    }
}

void CModel::setup () {
    if (m_initialized) {
	return;
    }

    CRenderable::setup ();

    if (!this->loadMesh ()) {
	sLog.error ("Model object ", this->getId (), " has no usable mesh - skipping");
	return;
    }

    for (auto& submesh : m_submeshes) {
	this->setupPass (submesh);
    }

    this->updateMatrices ();
    m_initialized = true;
}

bool CModel::loadMesh () {
    const auto stream = this->getScene ().getScene ().project.assetLocator->read (m_model.modelFile);
    const std::vector<char> data { std::istreambuf_iterator<char> (*stream), std::istreambuf_iterator<char> () };

    if (data.size () < 32 || std::memcmp (data.data (), "MDLV", 4) != 0) {
	sLog.error ("Not an MDLV model: ", m_model.modelFile);
	return false;
    }

    const size_t magicEnd = std::string_view (data.data (), data.size ()).find ('\0');
    if (magicEnd == std::string_view::npos) {
	sLog.error ("Unterminated MDLV magic in ", m_model.modelFile);
	return false;
    }

    uint32_t mdlvVersion = 0;
    if (magicEnd >= 8) {
	for (size_t digit = 4; digit < 8; digit++) {
	    const char ch = data[digit];
	    if (ch < '0' || ch > '9') {
		mdlvVersion = 0;
		break;
	    }
	    mdlvVersion = mdlvVersion * 10 + static_cast<uint32_t> (ch - '0');
	}
    }

    size_t offset = magicEnd + 1 + 2 * sizeof (uint32_t);
    if (offset + sizeof (uint32_t) > data.size ()) {
	sLog.error ("Truncated MDLV header in ", m_model.modelFile);
	return false;
    }

    uint32_t submeshCount = 0;
    std::memcpy (&submeshCount, data.data () + offset, sizeof (submeshCount));
    offset += sizeof (submeshCount);
    if (submeshCount == 0 || submeshCount > 16) {
	sLog.error ("Unexpected submesh count ", submeshCount, " in ", m_model.modelFile);
	return false;
    }

    const auto readU32 = [&data, &offset] (uint32_t& out) -> bool {
	if (offset + sizeof (uint32_t) > data.size ()) {
	    return false;
	}
	std::memcpy (&out, data.data () + offset, sizeof (out));
	offset += sizeof (uint32_t);
	return true;
    };

    GLint previousVAO = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &previousVAO);

    for (uint32_t index = 0; index < submeshCount; index++) {
	if (offset >= data.size ()) {
	    sLog.error ("Truncated submesh record ", index, " in ", m_model.modelFile);
	    break;
	}

	const auto* nameEnd
	    = static_cast<const char*> (std::memchr (data.data () + offset, 0, data.size () - offset));
	if (nameEnd == nullptr) {
	    sLog.error ("Unterminated submesh material name in ", m_model.modelFile);
	    break;
	}
	offset = static_cast<size_t> (nameEnd - data.data ()) + 1;

	if (offset + sizeof (uint32_t) + 6 * sizeof (float) > data.size ()) {
	    sLog.error ("Truncated submesh metadata in ", m_model.modelFile);
	    break;
	}
	offset += sizeof (uint32_t) + 6 * sizeof (float);

	uint32_t vertexTag = 0;
	if (!readU32 (vertexTag)) {
	    sLog.error ("Bad vertex tag in submesh ", index, " of ", m_model.modelFile);
	    break;
	}

	size_t vertexStride = 48;
	GLuint uvOffset = 40;
	if (vertexTag == 0x0180000fu) {
	    vertexStride = 80;
	    uvOffset = 72;
	} else if (vertexTag == 0u && mdlvVersion != 0 && mdlvVersion < 16) {
	    vertexStride = 52;
	    uvOffset = 44;
	} else if (vertexTag != 15u) {
	    sLog.error ("Unsupported MDLV vertex layout tag ", vertexTag, " in ", m_model.modelFile);
	    break;
	}

	uint32_t vertexBytes = 0;
	if (!readU32 (vertexBytes) || vertexBytes == 0 || vertexBytes % vertexStride != 0
	    || offset + vertexBytes > data.size ()) {
	    sLog.error ("Bad vertex block in submesh ", index, " of ", m_model.modelFile);
	    break;
	}
	const size_t verticesOffset = offset;
	offset += vertexBytes;

	uint32_t indexBytes = 0;
	if (!readU32 (indexBytes) || indexBytes == 0 || indexBytes % (sizeof (uint16_t) * 3) != 0
	    || offset + indexBytes > data.size ()) {
	    sLog.error ("Bad index block in submesh ", index, " of ", m_model.modelFile);
	    break;
	}
	const size_t indicesOffset = offset;
	offset += indexBytes;

	const size_t vertexCount = vertexBytes / vertexStride;
	const auto indexCount = static_cast<GLsizei> (indexBytes / sizeof (uint16_t));
	const auto* indices = reinterpret_cast<const uint16_t*> (data.data () + indicesOffset);
	bool valid = true;
	for (GLsizei i = 0; i < indexCount; i++) {
	    if (indices[i] >= vertexCount) {
		valid = false;
		break;
	    }
	}
	if (!valid) {
	    sLog.error ("Mesh index out of range in submesh ", index, " of ", m_model.modelFile);
	    continue;
	}

	Submesh submesh {};
	submesh.indexCount = indexCount;
	submesh.stride = static_cast<GLsizei> (vertexStride);
	submesh.uvOffset = uvOffset;

	if (index == 0) {
	    submesh.material = m_model.material.get ();
	} else if (index - 1 < m_model.extraMaterials.size ()) {
	    submesh.material = m_model.extraMaterials[index - 1].get ();
	}

	if (submesh.material == nullptr || submesh.material->passes.empty ()) {
	    sLog.error ("Submesh ", index, " of ", m_model.modelFile, " has no material pass");
	    continue;
	}

	glGenVertexArrays (1, &submesh.vao);
	glGenBuffers (1, &submesh.vbo);
	glGenBuffers (1, &submesh.ebo);

	glBindVertexArray (submesh.vao);
	glBindBuffer (GL_ARRAY_BUFFER, submesh.vbo);
	glBufferData (GL_ARRAY_BUFFER, vertexBytes, data.data () + verticesOffset, GL_STATIC_DRAW);
	glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, submesh.ebo);
	glBufferData (GL_ELEMENT_ARRAY_BUFFER, indexBytes, data.data () + indicesOffset, GL_STATIC_DRAW);

	m_submeshes.push_back (std::move (submesh));
    }

    glBindVertexArray (static_cast<GLuint> (previousVAO));
    return !m_submeshes.empty ();
}

void CModel::setupPass (Submesh& submesh) {
    const auto& firstPass = **submesh.material->passes.begin ();

    submesh.fboProvider = std::make_shared<FBOProvider> (this);
    submesh.passOverride = std::make_unique<ImageEffectPassOverride> ();

    // Keep authored material behavior, but enable the model-lighting branch when
    // the shader exposes it. The existing renderer already supplies ambient/skylight.
    if (!firstPass.combos.contains ("LIGHTING")) {
	submesh.passOverride->combos["LIGHTING"] = 1;
    }

    submesh.pass = new Effects::CPass (
	*this, submesh.fboProvider, firstPass, *submesh.passOverride, std::nullopt, std::nullopt
    );

    submesh.pass->setDestination (this->getScene ().getFBO ());
    submesh.pass->setInput (this->getTexture ());
    submesh.pass->setModelViewProjectionMatrix (&m_mvpMatrix);
    submesh.pass->setModelViewProjectionMatrixInverse (&m_mvpMatrixInverse);
    submesh.pass->setModelMatrix (&m_modelMatrix);
    submesh.pass->setViewProjectionMatrix (&m_viewProjectionMatrix);
    submesh.pass->addUniform ("g_NormalModelMatrix", &m_normalMatrix);
    submesh.pass->addUniform ("g_EyePosition", &m_eyePosition);

    const GLuint program = submesh.pass->getProgramID ();
    const GLsizei stride = submesh.stride;

    GLint previousVAO = 0;
    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &previousVAO);
    glBindVertexArray (submesh.vao);
    glBindBuffer (GL_ARRAY_BUFFER, submesh.vbo);
    glBindBuffer (GL_ELEMENT_ARRAY_BUFFER, submesh.ebo);

    const GLint locPosition = glGetAttribLocation (program, "a_Position");
    const GLint locNormal = glGetAttribLocation (program, "a_Normal");
    const GLint locTangent = glGetAttribLocation (program, "a_Tangent4");
    const GLint locTexCoord = glGetAttribLocation (program, "a_TexCoord");

    if (locPosition >= 0) {
	glEnableVertexAttribArray (locPosition);
	glVertexAttribPointer (locPosition, 3, GL_FLOAT, GL_FALSE, stride, nullptr);
    }
    if (locNormal >= 0) {
	glEnableVertexAttribArray (locNormal);
	glVertexAttribPointer (locNormal, 3, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*> (12));
    }
    if (locTangent >= 0) {
	glEnableVertexAttribArray (locTangent);
	glVertexAttribPointer (locTangent, 4, GL_FLOAT, GL_FALSE, stride, reinterpret_cast<void*> (24));
    }
    if (locTexCoord >= 0) {
	glEnableVertexAttribArray (locTexCoord);
	glVertexAttribPointer (
	    locTexCoord, 2, GL_FLOAT, GL_FALSE, stride,
	    reinterpret_cast<void*> (static_cast<uintptr_t> (submesh.uvOffset))
	);
    }

    glBindVertexArray (static_cast<GLuint> (previousVAO));

    Submesh* current = &submesh;
    submesh.pass->setGeometryCallback (
	[current] () {
	    glGetIntegerv (GL_VERTEX_ARRAY_BINDING, &current->prevVAO);
	    glBindVertexArray (current->vao);
	    glFrontFace (GL_CW);
	},
	[current] () { glDrawElements (GL_TRIANGLES, current->indexCount, GL_UNSIGNED_SHORT, nullptr); },
	[current] () {
	    glFrontFace (GL_CCW);
	    glBindVertexArray (static_cast<GLuint> (current->prevVAO));
	}
    );
}

namespace {
float bezierEase (const float t, const float x1, const float x2) {
    const auto bx = [x1, x2] (const float s) {
	const float inv = 1.0f - s;
	return 3.0f * inv * inv * s * x1 + 3.0f * inv * s * s * x2 + s * s * s;
    };

    float s = t;
    for (int i = 0; i < 6; i++) {
	const float inv = 1.0f - s;
	const float dx
	    = 3.0f * inv * inv * x1 + 6.0f * inv * s * (x2 - x1) + 3.0f * s * s * (1.0f - x2);
	if (std::abs (dx) < 1e-6f) {
	    break;
	}
	s -= (bx (s) - t) / dx;
	s = std::clamp (s, 0.0f, 1.0f);
    }

    const float inv = 1.0f - s;
    return 3.0f * inv * s * s + s * s * s;
}

float evalChannel (const AnimationChannel& channel, const float frame, const float fallback) {
    if (channel.keys.empty ()) {
	return fallback;
    }
    if (channel.keys.size () == 1 || frame <= channel.keys.front ().frame) {
	return channel.keys.front ().value;
    }
    if (frame >= channel.keys.back ().frame) {
	return channel.keys.back ().value;
    }

    for (size_t i = 0; i + 1 < channel.keys.size (); i++) {
	const auto& k0 = channel.keys[i];
	const auto& k1 = channel.keys[i + 1];
	if (frame < k0.frame || frame > k1.frame) {
	    continue;
	}
	const float span = std::max (k1.frame - k0.frame, 1e-6f);
	const float t = (frame - k0.frame) / span;
	const float x1 = std::clamp (k0.frontX * 0.5f, 0.0f, 1.0f);
	const float x2 = std::clamp (1.0f + k1.backX * 0.5f, 0.0f, 1.0f);
	return k0.value + (k1.value - k0.value) * bezierEase (t, x1, x2);
    }

    return fallback;
}
}

extern float g_Time;

glm::vec3 CModel::effectiveAngles () const {
    const glm::vec3 authored = m_model.angles->value->getVec3 ();
    const auto* animation = m_model.anglesAnimation.get ();
    if (animation == nullptr || animation->maxFrame <= 0.0f) {
	return authored;
    }

    const float frame = std::fmod (g_Time * PropertyAnimation::FPS, animation->maxFrame);
    return {
	evalChannel (animation->channels[0], frame, authored.x),
	evalChannel (animation->channels[1], frame, authored.y),
	evalChannel (animation->channels[2], frame, authored.z),
    };
}

void CModel::updateMatrices () {
    const auto& camera = this->getScene ().getCamera ();
    const float sceneWidth = camera.getWidth ();
    const float sceneHeight = camera.getHeight ();

    if (!camera.isOrthogonal ()) {
	const glm::vec3 origin = m_model.origin->value->getVec3 ();
	const glm::vec3 angles = this->effectiveAngles ();
	const glm::vec3 scale = m_model.scale->value->getVec3 ();

	m_modelMatrix = glm::translate (glm::mat4 (1.0f), origin);
	m_modelMatrix = glm::rotate (m_modelMatrix, angles.z, glm::vec3 (0, 0, 1));
	m_modelMatrix = glm::rotate (m_modelMatrix, angles.y, glm::vec3 (0, 1, 0));
	m_modelMatrix = glm::rotate (m_modelMatrix, angles.x, glm::vec3 (1, 0, 0));
	m_modelMatrix = glm::scale (m_modelMatrix, scale);

	m_viewProjectionMatrix = camera.getProjection () * camera.getLookAt ();
	m_eyePosition = camera.getEye ();
    } else {
	glm::vec3 origin = m_model.origin->value->getVec3 ();
	origin.x -= sceneWidth / 2.0f;
	origin.y = sceneHeight / 2.0f - origin.y;

	const glm::vec3 angles = this->effectiveAngles ();
	const glm::vec3 scale = m_model.scale->value->getVec3 ();

	m_modelMatrix = glm::translate (glm::mat4 (1.0f), origin);
	m_modelMatrix = glm::rotate (m_modelMatrix, -angles.z, glm::vec3 (0, 0, 1));
	m_modelMatrix = glm::rotate (m_modelMatrix, angles.y, glm::vec3 (0, 1, 0));
	m_modelMatrix = glm::rotate (m_modelMatrix, -angles.x, glm::vec3 (1, 0, 0));
	m_modelMatrix = glm::scale (m_modelMatrix, scale);
	m_modelMatrix = glm::scale (m_modelMatrix, glm::vec3 (1.0f, -1.0f, 1.0f));

	if (m_model.perspective) {
	    const float sceneFov = glm::radians (camera.getFov ());
	    const float overrideFov = camera.getOverrideFov ();
	    const float projectionFov = overrideFov > 0.0f ? glm::radians (overrideFov) : sceneFov;
	    const float eyeZ = (sceneHeight * 0.5f) / std::tan (projectionFov * 0.5f);
	    const float nearz = std::max (camera.getNearZ (), 1.0f);
	    const float farz = std::max (camera.getFarZ (), eyeZ + 10.0f * sceneHeight);

	    const glm::mat4 projection
		= glm::perspective (projectionFov, sceneWidth / sceneHeight, nearz, farz);
	    const glm::mat4 view = glm::lookAt (
		glm::vec3 (0.0f, 0.0f, eyeZ), glm::vec3 (0.0f), glm::vec3 (0.0f, 1.0f, 0.0f)
	    );
	    m_viewProjectionMatrix = projection * view;
	    m_eyePosition = glm::vec3 (0.0f, 0.0f, eyeZ);
	} else {
	    m_viewProjectionMatrix = camera.getProjection () * camera.getLookAt ();
	    m_eyePosition = glm::vec3 (0.0f, 0.0f, 1000.0f);
	}
    }

    m_mvpMatrix = m_viewProjectionMatrix * m_modelMatrix;
    m_mvpMatrixInverse = glm::inverse (m_mvpMatrix);
    m_normalMatrix = glm::inverseTranspose (glm::mat3 (m_modelMatrix));
}

void CModel::render () {
    if (!m_initialized || !m_model.visible->value->getBool ()) {
	return;
    }

    this->updateMatrices ();
    for (const auto& submesh : m_submeshes) {
	submesh.pass->render ();
    }
}

const float& CModel::getBrightness () const { return m_brightness; }
const float& CModel::getUserAlpha () const { return m_model.alpha->value->getFloat (); }
const float& CModel::getAlpha () const { return m_model.alpha->value->getFloat (); }
const glm::vec3& CModel::getColor () const { return m_model.color->value->getVec3 (); }
const glm::vec4& CModel::getColor4 () const {
    m_color4 = { this->getColor (), this->getAlpha () };
    return m_color4;
}
const glm::vec3& CModel::getCompositeColor () const { return this->getColor (); }
