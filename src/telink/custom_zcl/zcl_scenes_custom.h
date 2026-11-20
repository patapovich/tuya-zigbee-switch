#ifndef ZCL_SCENES_CUSTOM_H
#define ZCL_SCENES_CUSTOM_H

#include "zcl_include.h"

status_t zcl_scenes_custom_register(u8 endpoint, u16 manuCode, u8 attrNum,
                                    const zclAttrInfo_t attrTbl[], cluster_forAppCb_t cb);

#endif /* ZCL_SCENES_CUSTOM_H */
