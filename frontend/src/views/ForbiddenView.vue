<template>
  <div class="kb-page">
    <div class="kb-card kb-empty">
      <el-result icon="warning" title="权限不足" sub-title="当前账号没有访问该页面的权限。如需开通，请联系平台管理员。">
        <template #extra><el-button type="primary" @click="$router.replace('/chat')">返回 AI 问答</el-button></template>
      </el-result>
    </div>
  </div>
</template>
