<template>
  <el-select
    :model-value="modelValue" multiple filterable allow-create default-first-option clearable
    placeholder="选择或输入名称" class="full" :data-testid="testId"
    @update:model-value="(v: string[]) => emit('update:modelValue', v.filter((x) => /^[A-Za-z0-9_.-]{1,64}$/.test(x)))"
  >
    <el-option v-for="o in options" :key="o" :label="o" :value="o" />
  </el-select>
</template>

<script setup lang="ts">
defineProps<{ modelValue: string[]; options: string[]; testId?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string[]): void }>()
</script>

<style scoped>.full { width: 100%; }</style>
