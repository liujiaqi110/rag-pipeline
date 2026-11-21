<template>
  <div class="kb-page">
    <div class="kb-card kb-empty">
      <el-result icon="info" title="页面不存在" sub-title="资源不存在或无权访问。">
        <template #extra><el-button type="primary" @click="$router.replace('/chat')">返回首页</el-button></template>
      </el-result>
    </div>
  </div>
</template>
