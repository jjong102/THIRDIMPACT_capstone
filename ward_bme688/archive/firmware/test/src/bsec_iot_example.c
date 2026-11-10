/*!
 * @file bsec_iot_example.c
 *
 * @brief
 * Example for using of BSEC library in a fixed configuration with the BME68x sensor.
 * This works by running an endless loop in the bsec_iot_loop() function.
 */

/*!
 * @addtogroup bsec_examples BSEC Examples
 * @brief BSEC usage examples
 * @{*/

/**********************************************************************************************************************/
/* header files */
/**********************************************************************************************************************/

#include "bsec_integration.h"

/**********************************************************************************************************************/
/* functions */
/**********************************************************************************************************************/

/*!
 * @brief           Write operation in either Wire or SPI
 *
 * param[in]        reg_addr        register address
 * param[in]        reg_data_ptr    pointer to the data to be written
 * param[in]        data_len        number of bytes to be written
 * param[in]        intf_ptr        interface pointer
 *
 * @return          result of the bus communication function
 */
int8_t bus_write(uint8_t reg_addr, const uint8_t *reg_data_ptr, uint32_t data_len, void *intf_ptr)
{
    // ...
    // Please insert system specific function to write to the bus where BME68x is connected
    // ...
    return 0;
}

/*!
 * @brief           Read operation in either Wire or SPI
 *
 * param[in]        reg_addr        register address
 * param[out]       reg_data_ptr    pointer to the memory to be used to store the read data
 * param[in]        data_len        number of bytes to be read
 * param[in]        intf_ptr        interface pointer
 * 
 * @return          result of the bus communication function
 */
int8_t bus_read(uint8_t reg_addr, uint8_t *reg_data_ptr, uint32_t data_len, void *intf_ptr)
{
    // ...
    // Please insert system specific function to read from bus where BME68x is connected
    // ...
    return 0;
}

/*!
 * @brief           System specific implementation of sleep function
 *
 * @param[in]       t_us     Time in microseconds
 * @param[in]       intf_ptr Pointer to the interface descriptor
 * 
 * @return          none
 */
void sleep_n(uint32_t t_us, void *intf_ptr)
{
    // ...
    // Please insert system specific function sleep or delay for t_ms milliseconds
    // ...
}

/*!
 * @brief           Capture the system time in microseconds
 *
 * @return          system_current_time    current system timestamp in microseconds
 */
int64_t get_timestamp_us()
{
    int64_t system_current_time = 0;
    // ...
    // Please insert system specific function to retrieve a timestamp (in microseconds)
    // ...
    return system_current_time;
}

/*!
 * @brief           Handling of the ready outputs
 *
 * @param[in]       outputs               	output_t structure
 * @param[in]       bsec_status             value returned by the bsec_do_steps() call
 *
 * @return          none
 */
void output_ready(output_t *outputs, bsec_library_return_t bsec_status)
{
    // ...
    // Please insert system specific code to further process or display the BSEC outputs
    // ...
}

/*!
 * @brief           Load previous library state from non-volatile memory
 *
 * @param[in,out]   state_buffer    buffer to hold the loaded state string
 * @param[in]       n_buffer        size of the allocated state buffer
 *
 * @return          number of bytes copied to state_buffer
 */
uint32_t state_load(uint8_t *state_buffer, uint32_t n_buffer)
{
    // ...
    // Load a previous library state from non-volatile memory, if available.
    //
    // Return zero if loading was unsuccessful or no state was available, 
    // otherwise return length of loaded state string.
    // ...
    return 0;
}

/*!
 * @brief           Save library state to non-volatile memory
 *
 * @param[in]       state_buffer    buffer holding the state to be stored
 * @param[in]       length          length of the state string to be stored
 *
 * @return          none
 */
void state_save(const uint8_t *state_buffer, uint32_t length)
{
    // ...
    // Save the string some form of non-volatile memory, if possible.
    // ...
}
 
/*!
 * @brief           Load library config from non-volatile memory
 *
 * @param[in,out]   config_buffer    buffer to hold the loaded state string
 * @param[in]       n_buffer        size of the allocated state buffer
 *
 * @return          number of bytes copied to config_buffer
 */
uint32_t config_load(uint8_t *config_buffer, uint32_t n_buffer)
{
    // ...
    // Load a library config from non-volatile memory, if available.
    //
    // Return zero if loading was unsuccessful or no config was available, 
    // otherwise return length of loaded config string.
    // ...
    return 0;
}

/*!
 * @brief       Main function which configures BSEC library and then reads and processes the data from sensor based
 *              on timer ticks
 *
 * @return      result of the processing
 */
int main()
{
    return_values_init ret;
    struct bme68x_dev bme_dev;
	memset(&bme_dev,0,sizeof(bme_dev)); 
    /* Call to the function which initializes the BSEC library 
     * Switch on low-power mode and provide no temperature offset */
    ret = bsec_iot_init(BSEC_SAMPLE_RATE_LP, 0.0f, bus_write, bus_read, sleep_n, state_load, config_load, bme_dev, 0);
    if (ret.bme68x_status)
    {
        /* Could not intialize BME68x */
        return (int)ret.bme68x_status;
    }
    else if (ret.bsec_status)
    {
        /* Could not intialize BSEC library */
        return (int)ret.bsec_status;
    }
    
    /* Call to endless loop function which reads and processes data based on sensor settings */
    /* State is saved every 10.000 samples, which means every 10.000 * 3 secs = 500 minutes  */
    bsec_iot_loop(sleep_n, get_timestamp_us, output_ready, state_save, 10000);
    
    return 0;
}

/*! @}*/
