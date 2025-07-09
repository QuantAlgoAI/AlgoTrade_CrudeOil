#include <iostream>
#include "app.h"
#include <string>
#include <chrono>
#include <thread>


#ifdef WIN32
    #include <windows.h>
#else
    #include <unistd.h>
#endif // win32

using namespace std;
using namespace std::literals::chrono_literals;


App::App()
{
    x = 100;
    y = 200;

}

void App::say(char const *s){
    //printf("%s\n", s);
    cout << s << std::endl;
}

void App::error(char const *s){
    cerr << "ERROR: " << s << std::endl;
}

/**
 * @brief pause the execution
 * @param _time ms
 */
void App::wait(int _time){

    /*
#ifdef WIN32
    Sleep(milliseconds);
#else
    usleep(_time * 1000);
#endif // win32
*/

    std::this_thread::sleep_for(std::chrono::milliseconds(_time));

}
